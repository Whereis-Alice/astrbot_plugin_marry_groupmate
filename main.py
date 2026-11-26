import asyncio
import os
import time

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools

from .keyword_trigger import KeywordRouter, MatchMode
from .src.command.help import cmd_show_help
from .src.command.breakup import cmd_breakup, handle_breakup_response
from .src.command.draw import cmd_draw_wife
from .src.command.forced_marriage import cmd_force_marry
from .src.command.my_wife import cmd_show_history
from .src.command.pick_wife import cmd_pick_wife, handle_pick_response
from .src.command.propose import cmd_propose, handle_propose_response
from .src.command.relationdiagram import cmd_show_graph
from .src.command.rbqrank import cmd_rbq_ranking
from .src.command.proposal_rank import cmd_proposal_ranking
from .src.db import ProposalStore
from .src.affinity import AffinityService
from .src.command.affinity import cmd_affinity
from .src.command.reset_propose_cd import cmd_reset_propose_cd
from .src.platforms.user_profiles import remember_user_profile
from .src.constants import _DEFAULT_KEYWORD_ROUTES
from .src.core import (
    ACTIVE_USERS_SAVE_INTERVAL_SECONDS,
    ACTIVE_USERS_TRIM_INTERVAL_SECONDS,
    count_active_users,
    ensure_today_records,
    record_active,
    save_active_users,
    start_active_users_save_task,
    stop_active_users_save_task,
)
from .src.debug import debug_log, log_command_platform
from .src.debug_utils import run_debug_graph
from .src.i18n import tr
from .src.identity import DATABASE_NAME, PLUGIN_NAME
from .src.utils import is_allowed_group, load_json, match_qq_official_target_command, save_json


class MarryGroupmatePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        self.curr_dir = os.path.dirname(__file__)

        self._withdraw_tasks: set[asyncio.Task] = set()
        self._propose_timeout_tasks: set[asyncio.Task] = set()
        
        self.data_dir = os.fspath(StarTools.get_data_dir(PLUGIN_NAME))
        self.records_file = os.path.join(self.data_dir, "wife_records.json")
        self.active_file = os.path.join(self.data_dir, "active_users.json") 
        self.official_profiles_file = os.path.join(
            self.data_dir, "qq_official_profiles.json"
        )
        self.forced_file = os.path.join(self.data_dir, "forced_marriage.json")
        self.breakup_file = os.path.join(self.data_dir, "breakup_cooldowns.json")
        self.marriage_action_file = os.path.join(self.data_dir, "marriage_action_today.json")
        self.rbq_stats_file = os.path.join(self.data_dir, "rbq_stats.json")
        
        self.proposal_store = None
        self._proposal_cleanup_task = None
        try:
            self.proposal_store = ProposalStore(os.path.join(self.data_dir, DATABASE_NAME))
            self._proposal_cleanup_task = asyncio.create_task(self._proposal_cleanup_loop())
        except Exception as exc:
            logger.error(f"求婚统计数据库初始化失败: {exc}")
            
        self.records = load_json(self.records_file, {"date": "", "groups": {}})
        self.active_users = load_json(self.active_file, {})
        self.official_profiles = load_json(self.official_profiles_file, {})
        self.forced_records = load_json(self.forced_file, {})
        self.breakup_records = load_json(self.breakup_file, {})
        self.marriage_action_records = load_json(self.marriage_action_file, {})
        self.rbq_stats = load_json(self.rbq_stats_file, {})
        self._active_user_count = count_active_users(self.active_users)
        self._active_users_dirty = False
        self._active_users_last_save_at = time.time()
        self._active_users_save_interval_seconds = ACTIVE_USERS_SAVE_INTERVAL_SECONDS
        self._active_users_save_task: asyncio.Task | None = None
        self._active_users_last_trim_at = 0.0
        self._active_users_trim_interval_seconds = ACTIVE_USERS_TRIM_INTERVAL_SECONDS
        start_active_users_save_task(self)
        self.affinity = AffinityService(self, logger)
        self.affinity_dashboard = None
        try:
            from .src.web.dashboard import AffinityDashboard
            self.affinity_dashboard = AffinityDashboard(self)
            self.affinity_dashboard.register()
        except (ImportError, AttributeError) as exc:
            logger.warning(f"[marry_groupmate.affinity.page] 当前 AstrBot 未提供兼容的插件页面接口：{exc}")

        self._keyword_router = KeywordRouter(routes=_DEFAULT_KEYWORD_ROUTES)
        self._keyword_handlers = {
            "draw_wife": self._cmd_draw_wife,
            "show_history": self._cmd_show_history,
            "force_marry": self._cmd_force_marry,
            "show_graph": self._cmd_show_graph,
            "rbq_ranking": self.rbq_ranking,
            "proposal_ranking": self.proposal_ranking,
            "show_help": self._cmd_show_help,
            "propose_command": self.propose_command,
            "pick_wife": self._cmd_pick_wife,
            "show_affinity": self.show_affinity,
            "adjust_affinity": self.adjust_affinity,
            "breakup": self._cmd_breakup,
        }
        self._keyword_trigger_block_prefixes = ("/", "!", "！")
        logger.info(f"娶群友已加载。数据目录: {self.data_dir}")
        debug_log(
            self,
            "debug",
            f"debug enabled active_days={self.config.get('active_user_days', 30)} "
            f"active_file={self.active_file}",
        )

    def _get_keyword_trigger_mode(self) -> MatchMode:
        """读取无前缀关键词的匹配模式。"""
        raw = self.config.get("keyword_trigger_mode", "exact")
        try:
            return MatchMode(str(raw))
        except ValueError:
            return MatchMode.EXACT

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def keyword_trigger(self, event: AstrMessageEvent):
        if not is_allowed_group(str(event.get_group_id()), self.config):
            return
        # Official mentions may remain text, so CommandFilter sees the command
        # and <@openid> as one token. Route only this missing-space case here.
        keywords_enabled = self.config.get("keyword_trigger_enabled", False)
        action = (
            match_qq_official_target_command(event)
            if event.is_at_or_wake_command or keywords_enabled
            else None
        )
        if action:
            log_command_platform(self, event)
            event.stop_event()
            record_active(self, event)
            async for result in self._keyword_handlers[action](event):
                yield result
            return
        # 1. 检查开关
        if not keywords_enabled:
            return

        message_str = event.message_str
        if not message_str:
            return

        # 2. @bot / 唤醒前缀场景下跳过，交给 @filter.command 处理。
        #    原因：WakingCheckStage 会把 keyword_trigger（EventMessageTypeFilter 不检查
        #    is_at_or_wake_command）和对应的 CommandFilter handler 同时加入
        #    activated_handlers；命令 handler 需要停止事件，避免后续 LLM 阶段继续处理。
        if event.is_at_or_wake_command:
            return

        # 3. 如果消息本身就带了 / 或 !，说明是正规指令，交给 @filter.command 去处理
        if message_str.lstrip().startswith(self._keyword_trigger_block_prefixes):
            return
        # 3. 开始匹配关键词（例如抽老婆）
        mode = self._get_keyword_trigger_mode()
        route = self._keyword_router.match_route(message_str, mode=mode)
        # 兼容模式：如果没有精准匹配，尝试命令式匹配
        if route is None:
            route = self._keyword_router.match_command_route(message_str)
        if route:
            # 关键词命中后立即停止事件，避免插件回复后继续进入 LLM。
            log_command_platform(self, event)
            event.stop_event()
            # 记录活跃（既然说话了就要进池子）
            record_active(self, event)
            # 找到对应的函数，比如 _cmd_draw_wife
            handler = self._keyword_handlers.get(route.action)
            if handler:
                # 核心：手动运行你的函数并获取结果
                async for result in handler(event):
                    yield result
                
    @filter.event_message_type(filter.EventMessageType.ALL, priority=5)
    async def track_active(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        if not group_id or not is_allowed_group(str(group_id), self.config):
            return
        dashboard = getattr(self, "affinity_dashboard", None)
        if dashboard is not None:
            dashboard.profiles.observe(event)
        affinity = getattr(self, "affinity", None)
        if affinity is not None:
            affinity.observe(event)
        remember_user_profile(self, event)
        record_active(self, event)
        async for result in handle_breakup_response(self, event):
            yield result
        if event.is_stopped():
            return
        if not event.is_private_chat():
            async for result in handle_pick_response(self, event):
                yield result
            if not event.is_stopped():
                async for result in handle_propose_response(self, event):
                    yield result

    @filter.command("抽老婆", alias={"娶群友"})
    async def draw_wife(self, event: AstrMessageEvent):
        """从本群的活跃候选中抽取，别名为「娶群友」。"""
        log_command_platform(self, event)
        event.stop_event()
        async for result in self._cmd_draw_wife(event):
            yield result

    async def _cmd_draw_wife(self, event: AstrMessageEvent):
        async for result in cmd_draw_wife(self, event):
            yield result

    @filter.command("群友抽取记录")
    async def show_history(self, event: AstrMessageEvent):
        """查看本群今日的抽取记录和剩余次数。"""
        log_command_platform(self, event)
        event.stop_event()
        async for result in self._cmd_show_history(event):
            yield result

    async def _cmd_show_history(self, event: AstrMessageEvent):
        async for result in cmd_show_history(self, event):
            yield result

    @filter.command("解除群友关系")
    async def breakup(self, event: AstrMessageEvent):
        log_command_platform(self, event)
        event.stop_event()
        async for result in self._cmd_breakup(event):
            yield result

    async def _cmd_breakup(self, event: AstrMessageEvent):
        async for result in cmd_breakup(self, event):
            yield result

    @filter.command("强娶群友")
    async def force_marry(self, event: AstrMessageEvent):
        """强娶 + @要娶的那个人"""
        log_command_platform(self, event)
        event.stop_event()
        async for result in cmd_force_marry(self, event):
            yield result

    async def _cmd_force_marry(self, event: AstrMessageEvent):
        async for result in cmd_force_marry(self, event):
            yield result

    @filter.command("群友关系图")
    async def show_graph(self, event: AstrMessageEvent):
        log_command_platform(self, event)
        event.stop_event()
        async for result in cmd_show_graph(self, event):
            yield result

    async def _cmd_show_graph(self, event: AstrMessageEvent):
        async for result in cmd_show_graph(self, event):
            yield result

    @filter.command("群友被强娶榜")
    async def rbq_ranking(self, event: AstrMessageEvent):
        log_command_platform(self, event)
        event.stop_event()
        async for result in cmd_rbq_ranking(self, event):
            yield result

    @filter.command("群友求婚榜")
    async def proposal_ranking(self, event: AstrMessageEvent):
        log_command_platform(self, event)
        event.stop_event()
        async for result in cmd_proposal_ranking(self, event):
            yield result

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("群友重置记录")
    async def reset_records(self, event: AstrMessageEvent):
        log_command_platform(self, event)
        event.stop_event()
        async for result in self._cmd_reset_records(event):
            yield result

    async def _cmd_reset_records(self, event: AstrMessageEvent):
        if event.is_private_chat():
            yield event.plain_result(tr(self, "group_only"))
            return
        group_id = str(event.get_group_id())
        if not is_allowed_group(group_id, self.config):
            return
        ensure_today_records(self)
        self.records["groups"].pop(group_id, None)
        pick_cooldowns = self.records.get("pick_cooldowns")
        if isinstance(pick_cooldowns, dict):
            pick_cooldowns.pop(group_id, None)
        self.breakup_records.pop(group_id, None)
        save_json(self.records_file, self.records)
        save_json(self.breakup_file, self.breakup_records)
        yield event.plain_result(tr(self, "reset_records"))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("群友重置强娶")
    async def reset_force_cd(self, event: AstrMessageEvent):
        log_command_platform(self, event)
        event.stop_event()
        async for result in self._cmd_reset_force_cd(event):
            yield result

    async def _cmd_reset_force_cd(self, event: AstrMessageEvent):
        if event.is_private_chat():
            yield event.plain_result(tr(self, "group_only"))
            return
        group_id = str(event.get_group_id())
        if not is_allowed_group(group_id, self.config):
            return

        if hasattr(self, "forced_records") and group_id in self.forced_records:
            self.forced_records[group_id] = {}
            save_json(self.forced_file, self.forced_records)

            logger.info(f"[marry_groupmate] 已重置群 {group_id} 的强娶冷却时间")
            yield event.plain_result(tr(self, "reset_force_done"))
        else:
            yield event.plain_result(tr(self, "reset_force_empty"))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("群友重置求婚")
    async def reset_propose_cd(self, event: AstrMessageEvent):
        log_command_platform(self, event)
        event.stop_event()
        async for result in cmd_reset_propose_cd(self, event):
            yield result

    @filter.command("娶群友帮助")
    async def show_help(self, event: AstrMessageEvent):
        """查看娶群友的群聊玩法。"""
        log_command_platform(self, event)
        event.stop_event()
        async for result in cmd_show_help(self, event):
            yield result

    async def _cmd_show_help(self, event: AstrMessageEvent):
        async for result in cmd_show_help(self, event):
            yield result

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("群友关系图调试")
    async def debug_graph(self, event: AstrMessageEvent):
        log_command_platform(self, event)
        event.stop_event()
        '''
        调试关系图渲染
        '''
        # 直接调用外部函数，将 self (插件实例) 和 event 传进去
        async for result in run_debug_graph(self, event):
            yield result
        
    @filter.command("向群友求婚")
    async def propose_command(self, event: AstrMessageEvent):
        log_command_platform(self, event)
        event.stop_event()
        # 调用外部的发起求婚逻辑
        async for result in cmd_propose(self, event):
            yield result

    @filter.command("挑选群友")
    async def pick_wife(self, event: AstrMessageEvent):
        log_command_platform(self, event)
        event.stop_event()
        async for result in self._cmd_pick_wife(event):
            yield result

    async def _cmd_pick_wife(self, event: AstrMessageEvent):
        async for result in cmd_pick_wife(self, event):
            yield result

    @filter.command("群友好感度")
    async def show_affinity(self, event: AstrMessageEvent):
        event.stop_event()
        async for result in cmd_affinity(self, event):
            yield result

    @filter.command("群友调整好感度")
    async def adjust_affinity(self, event: AstrMessageEvent):
        event.stop_event()
        async for result in cmd_affinity(self, event, adjust=True):
            yield result

    async def _proposal_cleanup_loop(self):
        while True:
            await asyncio.sleep(3600)
            try:
                await self.proposal_store.cleanup()
            except Exception as exc:
                logger.error(f"清理过期求婚统计失败: {exc}")

    async def terminate(self):
        dashboard = getattr(self, "affinity_dashboard", None)
        if dashboard is not None:
            dashboard.close()
        affinity = getattr(self, "affinity", None)
        if affinity is not None:
            await affinity.close()
        if self._proposal_cleanup_task is not None:
            self._proposal_cleanup_task.cancel()
            await asyncio.gather(self._proposal_cleanup_task, return_exceptions=True)
        for task in tuple(self._propose_timeout_tasks):
            task.cancel()
        if self._propose_timeout_tasks:
            await asyncio.gather(*self._propose_timeout_tasks, return_exceptions=True)
        self._propose_timeout_tasks.clear()
        await stop_active_users_save_task(self)
        save_json(self.records_file, self.records)
        save_active_users(self, force_trim=True, force=True)
        save_json(self.forced_file, self.forced_records)
        save_json(self.breakup_file, self.breakup_records)
        save_json(self.marriage_action_file, self.marriage_action_records)
        save_json(self.rbq_stats_file, self.rbq_stats)

        # 取消尚未执行的撤回任务，避免插件卸载后仍调用协议端。
        for task in tuple(self._withdraw_tasks):
            task.cancel()
        self._withdraw_tasks.clear()
