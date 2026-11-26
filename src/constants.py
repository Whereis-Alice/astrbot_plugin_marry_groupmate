from ..keyword_trigger import KeywordRoute

_DEFAULT_KEYWORD_ROUTES = (
    KeywordRoute(keyword="抽老婆", action="draw_wife"),
    KeywordRoute(keyword="群友抽取记录", action="show_history"),
    KeywordRoute(keyword="解除群友关系", action="breakup"),
    KeywordRoute(keyword="强娶群友", action="force_marry"),
    KeywordRoute(keyword="群友关系图", action="show_graph"),
    KeywordRoute(keyword="群友被强娶榜", action="rbq_ranking"),
    KeywordRoute(keyword="群友求婚榜", action="proposal_ranking"),
    KeywordRoute(keyword="娶群友帮助", action="show_help"),
    KeywordRoute(keyword="向群友求婚", action="propose_command"),
    KeywordRoute(keyword="挑选群友", action="pick_wife"),
    KeywordRoute(keyword="群友好感度", action="show_affinity"),
    KeywordRoute(keyword="群友调整好感度", action="adjust_affinity"),
    KeywordRoute(keyword="娶群友", action="draw_wife"),
)

OFFICIAL_MENTION_ROUTES = {
    "强娶群友": "force_marry",
    "向群友求婚": "propose_command",
    "群友好感度": "show_affinity",
    "群友调整好感度": "adjust_affinity",
}
