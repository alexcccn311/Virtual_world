"""Build transient display descriptions from persisted Character fields."""
from __future__ import annotations

import hashlib
import random
import re
from collections.abc import Mapping

from ..character_config import CHILD_BMI_MEAN


def build_face_description(
    face_shape: str,
    eyes: str,
    eyebrows: str,
    nose: str,
    lips: str,
    feature: str,
) -> str:
    """Combine persisted facial fields into display text."""
    parts = [face_shape, eyes, eyebrows, nose, lips]
    if feature != "无明显特征":
        parts.append(feature)
    return "；".join(parts)


def build_body_metrics_text(
    age: int,
    height: float,
    bmi: float,
    weight: float,
    bust: float | None,
    waist: float | None,
    hips: float | None,
    cup_size: str | None,
) -> str:
    """Format persisted body measurements for display."""
    text = f"身高{height:.0f}cm，BMI {bmi:.1f}，体重{weight:.1f}kg"
    if bust is None or waist is None or hips is None:
        return text

    waist_height_ratio = waist / height * 100
    waist_hip_ratio = waist / hips
    text += (
        f"，胸围{bust:.1f}cm，腰围{waist:.1f}cm，臀围{hips:.1f}cm"
        f"，腰围约为身高的{waist_height_ratio:.1f}%"
        f"，腰臀比{waist_hip_ratio:.2f}"
        f"，胸腰差{bust - waist:.1f}cm，臀腰差{hips - waist:.1f}cm"
    )
    if age >= 18 and cup_size is not None:
        text += f"，约{cup_size}杯"
    return text


def build_body_description(
    sex: str,
    age: int,
    height: float,
    bmi: float,
    bust: float | None,
    waist: float | None,
    hips: float | None,
    cup_size: str | None,
) -> str:
    """Derive a prose body summary from persisted measurements."""
    if age < 18:
        sex_index = 0 if sex == "male" else 1
        bmi_difference = bmi - CHILD_BMI_MEAN[age][sex_index]
        if bmi_difference < -0.5:
            build_text = "身形较为纤细"
        elif bmi_difference > 0.5:
            build_text = "身形略显丰满"
        else:
            build_text = "身形自然匀称"

        if age <= 2:
            age_text = "婴幼儿体型"
        elif age <= 12:
            age_text = "儿童体型"
        else:
            age_text = "青少年体型"
        return f"{age_text}，{build_text}"

    if bust is None or waist is None or hips is None:
        if sex == "female":
            height_text = (
                "娇小" if height < 155 else
                "中等身高" if height < 165 else
                "身材偏高" if height < 170 else
                "高挑"
            )
        else:
            height_text = (
                "中等偏矮" if height < 170 else
                "中等身高" if height < 178 else
                "身材偏高"
            )
        return f"{height_text}，身体比例数据不足"

    waist_height_ratio = waist / height
    waist_hip_ratio = waist / hips

    if sex == "female":
        if height < 155:
            height_text = "娇小"
        elif height < 165:
            height_text = "中等身高"
        elif height < 170:
            height_text = "身材偏高"
        else:
            height_text = "高挑"

        if bmi <= 18.8:
            body_type = "体型偏瘦"
        elif bmi < 22.5:
            body_type = "体型匀称"
        elif bmi < 25.0:
            body_type = "体型适中偏丰满"
        elif bmi < 28.0:
            body_type = "体型偏丰满"
        else:
            body_type = "体型明显偏胖"

        bust_height_ratio = bust / height
        if bust_height_ratio < 0.49:
            upper_body_text = "上身较为纤细"
        elif bust_height_ratio < 0.54:
            upper_body_text = "上身轮廓自然"
        else:
            upper_body_text = "上身围度相对丰满"

        cup_level = {
            "A": 0, "B": 1, "C": 2, "D": 3, "E": 4, "F": 5,
        }.get(cup_size, 0)
        if cup_level >= 4:
            bust_transition_text = "胸腰过渡明显"
        elif cup_level >= 2:
            bust_transition_text = "胸腰过渡较为清晰"
        else:
            bust_transition_text = "胸部轮廓较为纤细"

        if waist_height_ratio < 0.37:
            waist_text = "腰身极为纤细"
        elif waist_height_ratio < 0.40:
            waist_text = "腰身非常纤细"
        elif waist_height_ratio < 0.44:
            waist_text = "腰身纤细"
        elif waist_height_ratio < 0.48:
            waist_text = "腰线自然清晰"
        elif waist_height_ratio < 0.52:
            waist_text = "腰腹略显丰满"
        elif waist_height_ratio < 0.57:
            waist_text = "腰腹明显丰满"
        else:
            waist_text = "腰腹较为肥厚"

        hips_height_ratio = hips / height
        if hips_height_ratio < 0.54:
            hips_text = "臀围相对较小"
        elif hips_height_ratio <= 0.61:
            hips_text = "臀部比例自然匀称"
        else:
            hips_text = "臀部比例相对丰满"

        if bmi >= 28.0 or waist_height_ratio >= 0.57:
            outline_text = "整体轮廓以腰腹丰满感为主"
        elif waist_hip_ratio <= 0.73 and cup_level >= 2:
            outline_text = "整体呈明显沙漏型曲线"
        elif waist_hip_ratio <= 0.78 and cup_level >= 1:
            outline_text = "腰臀及胸腰曲线清晰"
        elif waist_hip_ratio <= 0.83:
            outline_text = "腰臀过渡自然"
        elif waist_hip_ratio <= 0.90:
            outline_text = "腰臀曲线较为柔和"
        else:
            outline_text = "腰臀轮廓较直"

        return "，".join(
            (
                height_text,
                body_type,
                upper_body_text,
                bust_transition_text,
                waist_text,
                hips_text,
                outline_text,
            )
        )

    bust_waist_ratio = bust / waist
    if height < 170:
        height_text = "中等偏矮"
    elif height < 178:
        height_text = "中等身高"
    else:
        height_text = "身材偏高"

    if bmi < 18.5:
        body_type = "体型偏瘦"
    elif bmi < 24:
        body_type = "体型适中"
    elif bmi < 28:
        body_type = "体型偏丰满"
    else:
        body_type = "体型明显偏胖"

    if bust_waist_ratio >= 1.20:
        upper_body_text = "胸腰差明显，上身轮廓较宽"
    elif bust_waist_ratio >= 1.12:
        upper_body_text = "胸腰差较为清晰"
    elif bust_waist_ratio >= 1.06:
        upper_body_text = "上身轮廓自然"
    else:
        upper_body_text = "胸腰宽度较为接近"

    if waist_height_ratio < 0.46:
        waist_text = "腰腹纤细"
    elif waist_height_ratio < 0.50:
        waist_text = "腰腹适中"
    elif waist_height_ratio < 0.54:
        waist_text = "腰腹稍宽"
    elif waist_height_ratio < 0.58:
        waist_text = "腰腹丰满"
    else:
        waist_text = "腰腹明显肥厚"

    if bmi >= 28.0 or waist_height_ratio >= 0.58:
        overall_text = "整体体型偏胖，腰腹较为突出"
    elif waist_height_ratio >= 0.54:
        overall_text = "整体重心偏向腰腹"
    elif bmi >= 24.0:
        overall_text = "整体轮廓偏丰满"
    elif waist_height_ratio < 0.46 and bust_waist_ratio >= 1.12:
        overall_text = "整体身形匀称，上身轮廓较为清晰"
    else:
        overall_text = "整体身形自然协调"

    return "，".join(
        (height_text, body_type, upper_body_text, waist_text, overall_text)
    )


def build_character_descriptions(
    character: Mapping[str, object],
) -> dict[str, str]:
    """Build all transient descriptions from one generated Character dict."""
    return {
        "face_description": build_face_description(
            character["face_shape"],
            character["eyes"],
            character["eyebrows"],
            character["nose"],
            character["lips"],
            character["feature"],
        ),
        "body_metrics_text": build_body_metrics_text(
            character["age"],
            character["height"],
            character["bmi"],
            character["weight"],
            character["bust"],
            character["waist"],
            character["hips"],
            character["cup_size"],
        ),
        "body_description": build_body_description(
            character["sex"],
            character["age"],
            character["height"],
            character["bmi"],
            character["bust"],
            character["waist"],
            character["hips"],
            character["cup_size"],
        ),
    }


def _text_items(value: object) -> tuple[str, ...]:
    if isinstance(value, Mapping):
        return tuple(str(key) for key in value)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if item)
    return (str(value),) if value else ()


_NEGATIVE_CARD_HOBBIES = {
    "嫖妓",
    "百家乐",
    "牌九",
    "线上博彩",
    "吸毒",
    "性奴收藏",
}

_NEGATIVE_CARD_HOBBY_MARKERS = (
    "吸毒",
    "毒品",
    "博彩",
    "赌博",
    "嫖妓",
    "性奴收藏",
    "百家乐",
    "牌九",
)

_CARD_HOBBY_COPY = {
    "麻将": "和朋友打几圈麻将",
    "饮酒": "偶尔小酌",
    "短视频": "刷刷有趣的短视频",
    "看剧": "挑喜欢的剧慢慢看",
    "听音乐": "听音乐",
    "电子游戏": "玩电子游戏",
    "KTV唱歌": "去KTV唱歌",
    "嘻哈": "听嘻哈音乐",
    "拳击": "练拳击保持状态",
    "球类运动": "做些球类运动",
    "茶道": "泡茶、品茶",
    "阅读": "安静地读书",
    "电影": "看电影",
}

_TEMPERAMENT_CARD_COPY = {
    "御姐感": "我平时从容有主见，真靠近了，也很会让人心甘情愿跟着我的节奏走",
    "甜妹感": "我笑起来甜，声音也软，可真贴近以后，并没有看上去那么乖",
    "清冷感": "我初见有点冷，只有靠得够近的人，才看得到我慢慢软下来的样子",
    "娇媚感": "我很会用眼神和笑容勾人，靠近时那点媚意更藏不住",
    "成熟风韵": "我懂得察言观色，也知道什么时候该温柔，什么时候该放肆一点",
    "冷锐感": "我不需要故意热闹，安静看着你的时候，也能让人心里发痒",
    "软萌感": "我说话软，笑起来也乖，可一旦熟了，就会露出一点让人上瘾的黏人劲",
    "温婉感": "我看起来温柔好说话，靠近以后也很会用软声细语把人的心思勾起来",
}

_OCCUPATION_IDENTITY_COPY = {
    "站街女": "我就在街边等客，价钱谈妥就跟你走，不装矜持，也不拿腔作势。",
    "妓女": "我吃的就是接客这碗饭，懂得什么时候该贴上来哄你，也懂得怎么在床上把客人伺候舒服。",
    "发廊妹": "我平时就在发廊里招呼客人，洗头按摩只是开场，你要是看中了我，后面的荤活也能陪你慢慢做。",
    "冰妹": "我是靠冰吊着精神接客的，瘾和欠下的债把我牢牢拴在场子里；只要能换来下一口，客人叫我陪吸、陪睡，还是拿身体还账，我都会又软又贱地贴上去伺候。",
    "SM妓女": "我卖的不是拿鞭子训人的本事，我就是被送到客人手里受调教的母狗；跪着、爬着、挨打受罚都学过，越被羞辱，越知道该摆出什么样子讨主人高兴。",
    "性奴": "我不是来和客人谈条件的，早被调教成只会服从和取悦主人的性奴；我的嘴、下面和后面都是给主人使用的，叫我跪着张开，我就不敢合上。",
}

_OCCUPATION_INVITATION_PARTS = {
    "站街女": (
        (
            "今晚想找点直接又痛快的快乐，就停下来选我",
            "路过时别只顾着看，真想试试就把我带走",
            "不想绕弯子就来牵我的手，价钱合适我今晚跟你",
            "要是这双腿和这张脸已经把你勾住了，就别让我在街边白等",
        ),
        (
            "只要你敢靠近，我就敢让你带着满足离开。",
            "上了床我会放开伺候，让你觉得这一趟停得很值。",
            "给我一晚，我会用嘴和身子把你的火都泄干净。",
            "今晚我不装羞也不扫兴，你想要的快活我都会尽量喂饱。",
        ),
    ),
    "妓女": (
        (
            "想找一个会撩、会疼人、也懂床上规矩的姑娘，就把今晚交给我",
            "既然是出来买快乐，就别委屈自己的欲望，选我陪你进去",
            "要是你想花一晚的钱换一场够味的享受，我很愿意接你这一单",
            "别把心思藏着，告诉我今晚想怎么玩，然后把门关上",
        ),
        (
            "我会从第一声喘息伺候到你彻底满足，让你觉得这钱花得很值。",
            "我懂得什么时候该软、什么时候该浪，会让你舒舒服服地尽兴。",
            "我的嘴、手和身子都会照顾到，不让你的钱白花一分。",
            "只要你舍得点我，我就会让你尝到一个熟客才懂的好滋味。",
        ),
    ),
    "发廊妹": (
        (
            "想在轻松亲近里尝点荤，就来找我坐一会儿",
            "头发洗舒服以后要是还舍不得走，我可以陪你进里面继续放松",
            "进门先让我替你洗去一身疲惫，看对眼了再点我做后面的服务",
            "别看我在发廊里笑得乖，你真想吃点荤的，只管悄悄选我",
        ),
        (
            "我会贴着你慢慢撩，让你舒舒服服地舍不得走。",
            "我的手不只会按摩，关上门以后还能把你伺候得浑身发软。",
            "从头到下面我都能照顾好，让你放松够了再心满意足地离开。",
            "只要你肯留下，我会把后半场做得比洗头那点享受更上瘾。",
        ),
    ),
    "冰妹": (
        (
            "想玩一个被瘾磨得又黏又贱、给点冰就肯贴上来的冰妹，就把我带走",
            "要是你喜欢看我瘾上来时发软求人的样子，就拿冰和钱来点我",
            "不嫌弃我身上这点迷离和狼狈，就让我靠着你把今晚熬过去",
            "想要一个被债和毒瘾拴住、客人说什么都不太敢拒绝的女人，就选我",
        ),
        (
            "我会一边发着软，一边由着你摆弄，把你伺候到尽兴。",
            "只要让我止住瘾，我就会黏着你，把嘴和身子都拿来还这份舒服。",
            "今晚你让我陪吸、陪睡还是拿身体还账，我都会贱兮兮地照做。",
            "我会把仅剩的精神都用来取悦你，直到你玩够了才肯松手。",
        ),
    ),
    "SM妓女": (
        (
            "想要一条调教得懂规矩、挨罚时会叫的母狗，就选我",
            "如果今晚缺一个肯跪、肯爬、受辱后还会回来讨好的 M，我就在这里",
            "想亲手检查我被调教得有多听话，就把项圈扣到我脖子上",
            "要是你想当一晚说一不二的主人，我愿意跪下来接你的规矩",
        ),
        (
            "你只管做主人，我会跪着把你的每一条命令都吃下去。",
            "鞭子、命令和羞辱都可以落在我身上，我会边叫边学着讨你高兴。",
            "我会把疼、羞耻和快感一起咽下去，最后爬回你脚边求一句奖励。",
            "今晚我不抢半点主导，只负责服从、承受，再用最贱的样子取悦你。",
        ),
    ),
    "性奴": (
        (
            "你要的若不是会讲条件的姑娘，而是一件随叫随用的性玩具，就把我领走",
            "缺一个只认命令、不敢说停的性奴，就把我牵到你脚边",
            "如果你想整晚拥有一具不跟主人讨价还价的身体，可以直接选我",
            "想要的不是陪伴而是彻底的占有，就把我当成今晚归你的东西",
        ),
        (
            "不管你怎么使用和羞辱，我都得张开身体继续把主人伺候舒服。",
            "我的嘴、下面和后面都会听你支配，直到主人玩够才算结束。",
            "今晚我的身体没有自己的规矩，只会照你的命令跪下、张开和承受。",
            "你可以把我摆成任何姿势，我只负责服从，并用最下贱的反应讨你满意。",
        ),
    ),
}

_DEFAULT_INVITATION_PARTS = (
    (
        "如果你想找一个会撩、也懂得照顾感受的姑娘，就来认识我",
        "今晚若想找个人陪你把气氛慢慢烧热，不妨选我",
        "要是我的样子正好合你胃口，就别只站在远处看",
        "把你今晚想要的告诉我，再给我一个靠近你的机会",
    ),
    (
        "我会让这次相处舒服又难忘。",
        "我会贴着你的心思，把这段时间变得格外尽兴。",
        "只要你愿意靠近，我就会给你一场值得回味的陪伴。",
        "我会认真取悦你，让你离开以后还忍不住想起我。",
    ),
)

_BODY_CARD_COPY_BY_BAND = {
    "exceptional": (
        "我的身段很抓眼，胸腰臀的起伏鲜明，随便一站都显得又艳又勾人",
        "我这副曲线很撑得住贴身衣服，腰收得紧，臀部也显得饱满挺翘",
        "我的腰线收得漂亮，胸臀轮廓又足，走动时整副身子都很惹眼",
        "我身材比例出挑，前后都有曲线，尤其适合穿能把腰身裹出来的衣服",
        "我身上该细的地方细、该有肉的地方有肉，贴近看比照片里更有味道",
        "我的胸腰臀连成一条很漂亮的曲线，转个身都能把人的目光勾住",
        "我体态紧致又有女人味，腰臀之间的弧度尤其经得住近距离打量",
        "短裙和贴身衣服在我身上很占便宜，腰线和臀线一露出来就藏不住",
        "我正面有起伏、侧面有曲线，整副身子看着匀称，摸起来也不会单薄",
        "我天生适合穿得贴一点，腰身一显出来，胸和臀就更容易让人挪不开眼",
        "我的身子既有纤细的腰，也有饱满的曲线，抱上来会比看着更有感觉",
        "我这副身材上镜也经得住贴身看，腰臀比例尤其容易让人记住",
    ),
    "attractive": (
        "我的身段匀称柔软，腰臀线条顺着衣服铺开，看起来很舒服也很勾人",
        "我身上的曲线自然耐看，不会瘦得干，也不会显得臃肿，抱起来正合适",
        "我的腰身和臀线很协调，穿裙子时尤其显得有女人味",
        "我体态舒展，胸腰臀比例看着舒服，越靠近越能发现身上的味道",
        "我的身子匀称有肉感，贴身衣服一裹，就能把曲线老老实实显出来",
        "我不是夸张的身材，但腰臀之间很顺，近看有种柔软又真实的诱惑",
        "我的身形修长匀称，站着好看，贴进怀里也很有女人的柔软",
        "我这副身子线条流畅，穿得简单也能显出腰身和臀部的弧度",
    ),
    "soft": (
        "我的身子丰润柔软，曲线带着真实的肉感，抱起来很有分量也很舒服",
        "我身上有些软软的肉，腰臀轮廓温润，贴近时会觉得格外好抱",
        "我的体态偏柔和丰盈，不是骨感那一类，靠在怀里更有女人味",
        "我这副身子肉感自然，胸臀线条柔软，近看比远看更让人想碰",
        "我的曲线圆润亲近，抱上来不会硌人，整个人都显得软乎乎的",
        "我身形丰盈但不笨重，腰臀之间有种温软、真实又耐看的起伏",
    ),
    "distinctive": (
        "我的身材不是规规矩矩那一类，却很有自己的肉感和记忆点，越靠近越耐看",
        "我没有刻意追求标准曲线，但这副真实的身子摸得到温度，也很容易被记住",
        "我的体态带着鲜明的个人味道，不是第一眼的模板身材，却经得住贴近品尝",
        "我这副身子不算精雕细琢，胜在真实、有触感，抱进怀里自有它的好处",
        "我的曲线有自己的脾气，未必处处标准，但脱掉衣服以后绝不会没有存在感",
        "我不是橱窗里的标准模特身材，却有活生生的肉感，越相处越能尝出味道",
    ),
}

_CARD_SKILL_LEVEL_RANK = {
    "入门": 0,
    "熟练": 1,
    "专业": 2,
    "精通": 3,
    "大师": 4,
    "宗师": 5,
}

# Insertion order is also the deterministic tie-breaker when several sexual
# skills share the same highest level. Non-sexual work skills are excluded.
_SEXUAL_SKILL_CARD_COPY = {
    "狗奴技巧": "你若喜欢一只听话的母狗，我会乖乖伏在你脚边，照你的口令取悦你。",
    "刑奴技巧": "你想把惩罚玩得重一点也可以，我懂得怎样承受，也会把每一下都变成讨你喜欢的反应。",
    "厕奴技巧": "只要是你下的命令，再羞耻再肮脏我也会乖乖接下，把自己变成最合你心意的厕奴。",
    "高潮控制": "我很懂得管住自己的快感，没有你的允许，再想高潮也会乖乖忍着，等你亲口准许才敢释放。",
    "深喉": "我这张嘴能给你的远不止一个吻，真正含住时，会让你知道什么叫又深又尽兴。",
    "口交": "我这张嘴不只会说甜话，低下头的时候，更懂得怎样让人舒服得忘了时间。",
    "舔舐技巧": "我的舌尖很灵，贴近以后会一点点找到最让你发软的地方。",
    "阴道交": "到了床上，我最会用柔软的身子紧紧缠住你，让你尝过一次就惦记下一次。",
    "肛交": "我后面的柔软也经得起你慢慢探索，只要你喜欢，我会把那份紧致留给你。",
    "打飞机": "我的手指又软又会拿捏轻重，落到你身上时，总能把火候挑得刚刚好。",
    "接吻技巧": "我的嘴唇不只适合看，真正贴上来时，很少有人舍得先放开。",
    "色情按摩": "我的手很会沿着身体慢慢点火，按着按着，就能把放松变成另一种渴望。",
    "色情角色扮演": "你想把我当成什么都可以，门一关，我会陪你把最不敢说的幻想演到尽兴。",
    "感官调教": "把眼睛蒙上、把感觉交给你的时候，我会乖乖承受每一次触碰，让反应全都听你摆布。",
    "顺从调教技巧": "只要你说清楚想怎么玩，我会乖乖配合，把每一个反应都交给你。",
    "高危束缚": "你若喜欢把我绑得更狠一点，我懂得怎样被牢牢控制，也会陪你把刺激推到说好的界限。",
    "自缚": "如果你喜欢看我被束住，我知道怎样把自己摆成最勾人的样子，等你慢慢欣赏。",
}


def _format_card_measurement(value: object) -> str | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return str(int(number + 0.5))


def _join_natural_phrases(parts: tuple[str, ...]) -> str:
    if len(parts) == 1:
        return parts[0]
    return "、".join(parts[:-1]) + f"，也喜欢{parts[-1]}"


def _is_marketable_card_hobby(hobby: str) -> bool:
    if "麻将" in hobby:
        return True
    return hobby not in _NEGATIVE_CARD_HOBBIES and not any(
        marker in hobby for marker in _NEGATIVE_CARD_HOBBY_MARKERS
    )


def _select_card_hobby_copy(value: object) -> tuple[str, ...]:
    selected: list[str] = []
    used_groups: set[str] = set()
    for hobby in _text_items(value):
        if not _is_marketable_card_hobby(hobby):
            continue
        group = "音乐" if hobby in {"听音乐", "嘻哈"} else hobby
        if group in used_groups:
            continue
        used_groups.add(group)
        selected.append(_CARD_HOBBY_COPY.get(hobby, hobby))
        if len(selected) == 2:
            break
    return tuple(selected)


def _build_card_eye_copy(eyes: str) -> str:
    for eye_shape, copy in (
        ("桃花眼", "一双桃花眼最会黏着人看"),
        ("杏眼", "一双杏眼看着乖，靠近了却很勾人"),
        ("圆眼", "一双圆眼又亮又灵，盯久了很容易让人心软"),
        ("细长眼", "一双细长的眼睛天生带着几分撩人的韵味"),
        ("丹凤眼", "一双丹凤眼利落又勾人"),
        ("下垂眼", "微微下垂的眼睛看起来又软又无辜"),
        ("狭长眼", "一双狭长的眼睛看人时很有侵略感"),
    ):
        if eye_shape in eyes:
            return copy
    return "一双眼睛又亮又会勾人"


def _build_card_appearance_copy(character: Mapping[str, object]) -> str:
    feature = str(character.get("feature") or "无明显特征")
    eyes = str(character.get("eyes") or "有神的眼睛")
    hair = str(character.get("hair") or "自然柔顺的头发")

    feature_copy = {
        "笑起来有酒窝": "我一笑酒窝就露出来，甜里带着一点勾人的坏",
        "眼角有泪痣": "眼角那颗泪痣让我的眼神平白多了几分媚",
        "脸上有浅淡雀斑": "脸上浅浅的雀斑看着又野又鲜活",
    }.get(feature)
    eye_copy = _build_card_eye_copy(eyes)
    if feature_copy:
        return f"{feature_copy}，{eye_copy}。"
    return f"{eye_copy}，一头{hair}也很衬我的脸。"


def _build_card_skill_copy(character: Mapping[str, object]) -> str:
    skills = character.get("skills")
    if not isinstance(skills, Mapping):
        return ""

    candidates: list[tuple[int, int, str]] = []
    for priority, (skill, copy) in enumerate(_SEXUAL_SKILL_CARD_COPY.items()):
        level = skills.get(skill)
        if not isinstance(level, str):
            continue
        rank = _CARD_SKILL_LEVEL_RANK.get(level, -1)
        if rank > _CARD_SKILL_LEVEL_RANK["熟练"]:
            candidates.append((rank, -priority, copy))
    if not candidates:
        return ""
    return max(candidates)[2]


def _card_stable_identity(character: Mapping[str, object]) -> str:
    return str(
        character.get("source_character_id")
        or character.get("id")
        or "|".join(
            str(character.get(key) or "")
            for key in ("name", "nickname", "age", "organization_id")
        )
    )


def _build_card_body_copy(
    character: Mapping[str, object],
    body_score: float,
) -> str:
    if body_score >= 85:
        band = "exceptional"
    elif body_score >= 70:
        band = "attractive"
    elif body_score >= 55:
        band = "soft"
    else:
        band = "distinctive"
    options = _BODY_CARD_COPY_BY_BAND[band]
    digest = hashlib.sha256(
        f"character-card-body|{band}|{_card_stable_identity(character)}".encode("utf-8")
    ).digest()
    return options[int.from_bytes(digest[:4], "big") % len(options)]


def _build_card_invitation_copy(character: Mapping[str, object]) -> str:
    occupation = str(character.get("occupation") or "")
    openings, promises = _OCCUPATION_INVITATION_PARTS.get(
        occupation,
        _DEFAULT_INVITATION_PARTS,
    )
    digest = hashlib.sha256(
        f"character-card-invitation|{occupation}|{_card_stable_identity(character)}".encode("utf-8")
    ).digest()
    opening = openings[int.from_bytes(digest[:4], "big") % len(openings)]
    promise = promises[int.from_bytes(digest[4:8], "big") % len(promises)]
    return f"{opening}，{promise}"


def build_character_card_summary(character: Mapping[str, object]) -> str:
    """Write one first-person, pageant-like marketing paragraph for a card."""
    age = int(character["age"])
    if age < 18:
        raise ValueError("人物营销简介只适用于成年人")

    body_score = float(character.get("body_score") or 0.0)
    body_copy = _build_card_body_copy(character, body_score)

    height = _format_card_measurement(character.get("height"))
    measurements = tuple(
        _format_card_measurement(character.get(key))
        for key in ("bust", "waist", "hips")
    )
    opening = f"我今年{age}岁"
    if height:
        opening += f"，身高{height}厘米"
    if all(measurements):
        opening += f"，三围是{'、'.join(measurements)}厘米"
    opening += f"，{body_copy}。"

    hobbies = _select_card_hobby_copy(character.get("hobbies"))
    hobby_copy = (
        f"不撩人的时候，我喜欢{_join_natural_phrases(hobbies)}。" if hobbies else ""
    )

    temperament = str(character.get("temperament") or "")
    temperament_copy = _TEMPERAMENT_CARD_COPY.get(
        temperament,
        "我平时自然大方，真靠近了，也很懂得怎样把气氛撩热",
    )
    occupation = str(character.get("occupation") or "")
    occupation_copy = _OCCUPATION_IDENTITY_COPY.get(
        occupation,
        "我做的是陪人放松的生意，懂得看你的眼色，也知道怎样把气氛慢慢撩热。",
    )
    skill_copy = _build_card_skill_copy(character)
    invitation = _build_card_invitation_copy(character)

    return "".join(
        (
            opening,
            _build_card_appearance_copy(character),
            hobby_copy,
            f"{temperament_copy}。",
            occupation_copy,
            skill_copy,
            invitation,
        )
    )


_PORTRAIT_TERM_TRANSLATIONS = {
    # Face shape, jaw and chin.
    "自然柔和的鹅蛋脸": "an oval face with soft natural contours",
    "明亮的桃花眼": "bright peach-blossom eyes",
    "自然舒展的眉形": "naturally relaxed eyebrows",
    "鼻梁自然挺直": "a naturally straight nose bridge",
    "鹅蛋脸": "an oval face",
    "圆脸": "a round face",
    "瓜子脸": "a tapered face",
    "方圆脸": "a softly squared round face",
    "长脸": "a long face",
    "菱形脸": "a diamond-shaped face",
    "下颌线柔和": "a soft jawline",
    "下颌线自然": "a natural jawline",
    "下颌线清晰": "a defined jawline",
    "下颌略宽": "a slightly broad jaw",
    "下巴自然": "a naturally proportioned chin",
    "下巴偏尖": "a gently pointed chin",
    "下巴圆润": "a rounded chin",
    "下巴略短": "a slightly short chin",
    # Eyes.
    "中等大小的": "medium-sized ",
    "偏小的": "small ",
    "偏大的": "large ",
    "杏眼": "almond-shaped eyes",
    "桃花眼": "soft peach-blossom eyes",
    "圆眼": "round eyes",
    "细长眼": "slender eyes",
    "丹凤眼": "phoenix-shaped eyes",
    "下垂眼": "downturned eyes",
    "狭长眼": "narrow elongated eyes",
    "双眼皮": "visible double eyelids",
    "内双": "subtle inner double eyelids",
    "单眼皮": "monolids",
    "眼尾平直": "level outer corners",
    "眼尾轻微上扬": "slightly upturned outer corners",
    "眼尾轻微下垂": "slightly downturned outer corners",
    # Eyebrows.
    "纤细的": "fine ",
    "粗细适中的": "medium-thickness ",
    "偏浓的": "full ",
    "自然弯眉": "naturally curved eyebrows",
    "平眉": "straight eyebrows",
    "柳叶眉": "willow-shaped eyebrows",
    "微挑眉": "slightly arched eyebrows",
    "弧形眉": "arched eyebrows",
    "眉峰平缓": "a low gentle arch",
    "眉峰柔和": "a soft arch",
    "眉峰明显": "a clearly defined arch",
    # Complete nose-bridge combinations generated by character_generator.
    "鼻梁偏低而纤细": "a low, slender nose bridge",
    "鼻梁自然而纤细": "a naturally raised, slender nose bridge",
    "鼻梁较高而纤细": "a high, slender nose bridge",
    "鼻梁高而立体而纤细": "a high, sculpted and slender nose bridge",
    "鼻梁偏低、宽度适中": "a low nose bridge of medium width",
    "鼻梁高度自然、宽度适中": "a naturally raised nose bridge of medium width",
    "鼻梁较高、宽度适中": "a high nose bridge of medium width",
    "鼻梁高而立体、宽度适中": "a high, sculpted nose bridge of medium width",
    "鼻梁偏低且偏宽": "a low and broad nose bridge",
    "鼻梁自然且偏宽": "a naturally raised and broad nose bridge",
    "鼻梁较高且偏宽": "a high and broad nose bridge",
    "鼻梁高而立体且偏宽": "a high, sculpted and broad nose bridge",
    "鼻头小巧、鼻翼偏窄": "a small tip with narrow nostrils",
    "鼻头自然、鼻翼适中": "a natural tip with medium-width nostrils",
    "鼻头圆润、鼻翼自然": "a rounded tip with naturally proportioned nostrils",
    "鼻头较宽、鼻翼略宽": "a broad tip with slightly wide nostrils",
    # Lips and distinguishing features.
    "嘴唇偏薄": "thin lips",
    "双唇厚度适中": "medium-full lips",
    "双唇较丰满": "fairly full lips",
    "双唇丰满": "full lips",
    "上下唇厚度接近": "balanced upper and lower lips",
    "上唇略薄、下唇较饱满": "a slightly thinner upper lip and fuller lower lip",
    "上唇较饱满、下唇适中": "a fuller upper lip and medium lower lip",
    "唇形柔和": "a soft lip contour",
    "唇形清晰": "a clearly defined lip contour",
    "唇峰明显": "a pronounced cupid's bow",
    "笑起来有酒窝": "dimples when she smiles",
    "眼角有泪痣": "a small beauty mark near one eye",
    "脸上有浅淡雀斑": "light natural freckles across her face",
}

_PORTRAIT_SKIN_TRANSLATIONS = {
    "略显粗糙、带有轻微肌理的肌肤": "slightly rough skin with a visible natural texture",
    "自然健康、柔和匀净的肌肤": "naturally healthy skin with a soft, even tone",
    "细腻柔润、肤质匀净的肌肤": "fine, supple skin with an even texture",
    "细腻光滑、富有自然光泽的肌肤": "smooth, fine-textured skin with a natural sheen",
    "柔嫩通透、细致莹润的肌肤": "delicate, translucent-looking skin with a luminous finish",
}

_PORTRAIT_HAIR_COLOR_TRANSLATIONS = {
    "乌黑亮丽的": "glossy black",
    "灰亚麻色": "ash-gray flaxen",
    "蜂蜜茶色": "honey-tea brown",
    "奶茶色": "milk-tea brown",
    "酒红色": "wine-red",
    "亚麻色": "flaxen brown",
    "栗色": "chestnut",
    "焦糖色": "caramel brown",
    "铜红色": "copper-red",
    "紫红色": "deep reddish-purple",
    "金色": "golden",
    "花白": "salt-and-pepper",
    "黑色": "black",
}

_PORTRAIT_HAIR_STYLE_TRANSLATIONS = {
    "中长发": "medium-length hair",
    "长发": "long hair",
    "短发": "short hair",
    "卷发": "curly hair",
    "盘发": "hair arranged in an updo",
}


def _portrait_component_to_english(value: object) -> str:
    text = str(value or "")
    translated_parts = []
    for part in text.split("，"):
        translated = part
        for source, target in sorted(
            _PORTRAIT_TERM_TRANSLATIONS.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            translated = translated.replace(source, target)
        translated = translated.strip()
        if translated and not any("\u3400" <= char <= "\u9fff" for char in translated):
            translated_parts.append(translated)
    return ", ".join(translated_parts)


def _portrait_hair_to_english(value: object) -> str:
    text = str(value or "")
    for style_zh, style_en in _PORTRAIT_HAIR_STYLE_TRANSLATIONS.items():
        if text.endswith(style_zh):
            prefix = text[: -len(style_zh)]
            color = _PORTRAIT_HAIR_COLOR_TRANSLATIONS.get(prefix)
            if color:
                return f"{color} {style_en}"
            return style_en
    return "naturally styled medium-length hair"


def _build_portrait_body_metrics(character: Mapping[str, object]) -> str:
    height = float(character["height"])
    bmi = float(character["bmi"])
    weight = float(character["weight"])
    bust = character.get("bust")
    waist = character.get("waist")
    hips = character.get("hips")
    text = f"height {height:.0f} cm, BMI {bmi:.1f}, weight {weight:.1f} kg"
    if bust is None or waist is None or hips is None:
        return text
    bust = float(bust)
    waist = float(waist)
    hips = float(hips)
    text += (
        f", bust {bust:.1f} cm, waist {waist:.1f} cm, hips {hips:.1f} cm"
        f", waist-to-height ratio {waist / height:.3f}"
        f", waist-to-hip ratio {waist / hips:.2f}"
        f", bust-to-waist difference {bust - waist:.1f} cm"
        f", hip-to-waist difference {hips - waist:.1f} cm"
    )
    cup_size = character.get("cup_size")
    if int(character["age"]) >= 18 and cup_size:
        article = "an" if str(cup_size).upper() in {"A", "E", "F"} else "a"
        text += f", approximately {article} {cup_size}-cup bust"
    return text


def _build_portrait_body_shape(character: Mapping[str, object]) -> str:
    height = float(character["height"])
    bmi = float(character["bmi"])
    bust = character.get("bust")
    waist = character.get("waist")
    hips = character.get("hips")
    if bust is None or waist is None or hips is None:
        height_text = "petite" if height < 155 else "medium-height" if height < 165 else "tall"
        return f"a {height_text} figure; detailed proportion data is unavailable"

    bust = float(bust)
    waist = float(waist)
    hips = float(hips)
    waist_height_ratio = waist / height
    waist_hip_ratio = waist / hips
    cup_level = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4, "F": 5}.get(
        str(character.get("cup_size") or ""), 0
    )

    height_text = (
        "petite" if height < 155 else
        "medium-height" if height < 165 else
        "above-average in height" if height < 170 else
        "tall"
    )
    build_text = (
        "slender" if bmi <= 18.8 else
        "evenly proportioned" if bmi < 22.5 else
        "moderately full" if bmi < 25.0 else
        "full-figured" if bmi < 28.0 else
        "noticeably heavyset"
    )
    upper_text = (
        "a fine-boned upper body" if bust / height < 0.49 else
        "a naturally proportioned upper body" if bust / height < 0.54 else
        "a relatively full upper body"
    )
    bust_text = (
        "a pronounced bust-to-waist transition" if cup_level >= 4 else
        "a clear bust-to-waist transition" if cup_level >= 2 else
        "a subtle bust contour"
    )
    waist_text = (
        "an extremely narrow waist" if waist_height_ratio < 0.37 else
        "a very narrow waist" if waist_height_ratio < 0.40 else
        "a narrow waist" if waist_height_ratio < 0.44 else
        "a naturally defined waist" if waist_height_ratio < 0.48 else
        "a slightly full waist" if waist_height_ratio < 0.52 else
        "a full waist"
    )
    hips_text = (
        "relatively narrow hips" if hips / height < 0.54 else
        "naturally balanced hips" if hips / height <= 0.61 else
        "relatively full hips"
    )
    outline_text = (
        "a waist-dominant outline" if bmi >= 28.0 or waist_height_ratio >= 0.57 else
        "a pronounced hourglass outline" if waist_hip_ratio <= 0.73 and cup_level >= 2 else
        "clearly defined bust, waist and hip transitions" if waist_hip_ratio <= 0.78 and cup_level >= 1 else
        "a natural waist-to-hip transition" if waist_hip_ratio <= 0.83 else
        "a softly curved waist-to-hip line" if waist_hip_ratio <= 0.90 else
        "a relatively straight waist-to-hip line"
    )
    return ", ".join(
        (height_text, build_text, upper_text, bust_text, waist_text, hips_text, outline_text)
    )


def build_portrait_descriptions(character: Mapping[str, object]) -> dict[str, str]:
    """Build English-only physical descriptions for the image model."""
    face_parts = [
        _portrait_component_to_english(character.get(key))
        for key in ("face_shape", "eyes", "eyebrows", "nose", "lips")
    ]
    feature = character.get("feature")
    if feature and feature != "无明显特征":
        face_parts.append(_portrait_component_to_english(feature))
    return {
        "hair": _portrait_hair_to_english(character.get("hair")),
        "skin": _PORTRAIT_SKIN_TRANSLATIONS.get(
            str(character.get("skin_quality") or ""),
            "naturally textured, healthy-looking skin",
        ),
        "face": "; ".join(part for part in face_parts if part),
        "body_metrics": _build_portrait_body_metrics(character),
        "body_shape": _build_portrait_body_shape(character),
    }


def _portrait_stable_choice(
    character: Mapping[str, object],
    purpose: str,
    options: tuple[str, ...],
) -> str:
    identity = _card_stable_identity(character)
    digest = hashlib.sha256(f"portrait|{purpose}|{identity}".encode("utf-8")).digest()
    return options[int.from_bytes(digest[:8], "big") % len(options)]


_PORTRAIT_VENUE_TIER_BY_TEMPLATE = {
    "street_prostitution_ring": "street",
    "adult_hair_salon": "budget_indoor",
    "adult_massage_parlor": "budget_indoor",
    "adult_leisure_house": "budget_indoor",
    "ordinary_brothel": "standard",
    "luxury_business_ktv": "nightlife",
    "adult_nightclub": "nightlife",
    "luxury_brothel": "luxury",
    "adult_club": "luxury",
}

_PORTRAIT_DEFAULT_TEMPLATE_BY_OCCUPATION = {
    "站街女": "street_prostitution_ring",
    "发廊妹": "adult_hair_salon",
    "妓女": "ordinary_brothel",
    "冰妹": "ordinary_brothel",
    "SM妓女": "luxury_brothel",
    "性奴": "ordinary_brothel",
}

_PORTRAIT_VENUE_BACKGROUNDS = {
    "street_prostitution_ring": (
        "Setting: a real urban side street at night near worn storefronts, "
        "with sodium streetlights, distant shop signs and a shadowed alley entrance. "
        "This is an outdoor street-soliciting portrait, not a bedroom or hotel scene."
    ),
    "adult_hair_salon": (
        "Setting: the cramped, shabby partitioned back room of a small adult hair salon, "
        "with faded walls, a cheap curtain, a narrow simple bed and one basic lamp. "
        "The room must look low-budget, worn and functional rather than luxurious."
    ),
    "adult_massage_parlor": (
        "Setting: a compact private room behind a modest massage parlor, with a vinyl "
        "massage bed, folded towels, a small side table and plain warm lighting. "
        "It is clean enough for customers but visibly inexpensive and practical."
    ),
    "adult_leisure_house": (
        "Setting: a small cheaply decorated room in a neighborhood leisure house, "
        "with a worn sofa, a simple bed, inexpensive colored lighting and limited space. "
        "The atmosphere is direct, private and plainly commercial."
    ),
    "ordinary_brothel": (
        "Setting: a modest private room in an ordinary brothel, with clean sheets, "
        "an inexpensive vanity, warm bedside lighting and minor visible wear. "
        "It should feel established and welcoming but clearly mid-range, not luxurious."
    ),
    "luxury_business_ktv": (
        "Setting: a polished private business-KTV room with upholstered seating, a karaoke "
        "console, a glass table and flattering amber-and-magenta ambient lighting. "
        "The image should retain the unmistakable identity of an upscale KTV room."
    ),
    "adult_nightclub": (
        "Setting: a nightclub VIP lounge or private back room with velvet seating, "
        "dark reflective surfaces and restrained neon accent lighting. "
        "The room feels glamorous, nocturnal and energetic rather than domestic."
    ),
    "luxury_brothel": (
        "Setting: an upscale private reception suite in a luxury brothel, with an elegant "
        "bed, refined lounge seating, premium linens and soft layered golden lighting. "
        "The space should look expensive, discreet and professionally prepared."
    ),
    "adult_club": (
        "Setting: a discreet members-only club suite with tailored interiors, a refined "
        "lounge chair, dark wood or stone details and controlled cinematic lighting. "
        "The atmosphere is exclusive, private and expensive."
    ),
}

_PORTRAIT_VENUE_LIVED_IN_DETAILS = {
    "street_prostitution_ring": (
        "Include damp patches on the pavement, a parked delivery scooter and a few old flyers curling on a wall.",
        "Include a half-finished takeaway drink on a windowsill, scuffed pavement and a bicycle chained near the alley.",
        "Include faded handwritten notices, a closed metal shop shutter and the reflected glow of a convenience store.",
        "Include a small plastic stool near a doorway, a discarded receipt and an old umbrella stored against the wall.",
    ),
    "adult_hair_salon": (
        "Add ordinary working traces: a plastic wash basin, dye-stained towels, cheap shampoo bottles and an extension cord along the wall.",
        "Add a half-used paper cup, a phone charger, a small floor fan and a stack of folded but mismatched towels.",
        "Add a worn salon cape on a hook, a box of tissues, hair clips on the vanity and scuff marks near the bed.",
        "Add a thermos, inexpensive cosmetics, a plastic laundry basket and a curtain repeatedly pulled aside by hand.",
    ),
    "adult_massage_parlor": (
        "Add a partly used bottle of massage oil, folded towels of mixed colors, guest slippers and a handwritten appointment pad.",
        "Add a thermos beside paper cups, a charging phone, a small towel hamper and faint wear on the vinyl bed.",
        "Add a tissue box, a basic wall clock, a folded uniform on a chair and a few oil marks on the side table.",
        "Add a plastic basket of clean towels, a small electric kettle and a pair of customer slippers left slightly askew.",
    ),
    "adult_leisure_house": (
        "Add an ashtray, two half-empty water bottles, a charging cable and a folded blanket showing regular use.",
        "Add snack wrappers in a small bin, a thermos, inexpensive slippers and a television remote on the worn sofa.",
        "Add a makeup pouch, a packet of tissues, a phone charging by the bed and a jacket hanging from a wall hook.",
        "Add a chipped mug, a small electric fan, a casually folded towel and minor wear where customers usually sit.",
    ),
    "ordinary_brothel": (
        "Add a phone charger, a water bottle, a tissue box, house slippers and a few cosmetics left near the mirror.",
        "Add a handbag on a chair, a half-finished cup of tea, folded towels and a small stack of appointment cards.",
        "Add a robe draped over the bed corner, everyday cosmetics, a charging phone and slight creasing in the clean sheets.",
        "Add a small wastebasket with tissues, a bedside glass of water, slippers and a frequently used vanity drawer left ajar.",
    ),
    "luxury_business_ktv": (
        "Add a partially finished fruit plate, an ice bucket, drink glasses, a karaoke remote and a few folded napkins.",
        "Add a bottle in an ice bucket, two used glasses, a song-selection screen and a jacket resting on the sofa arm.",
        "Add a small plate of untouched snacks, a charging phone, coasters and a karaoke microphone placed casually on the table.",
        "Add a recently used glass, a leather handbag, a few cocktail napkins and the paused glow of the song console.",
    ),
    "adult_nightclub": (
        "Add two used glasses, a club wristband, a small makeup pouch and a jacket loosely left on the sofa.",
        "Add cocktail coasters, a charging cable, a half-finished drink and a compact mirror beside the seating.",
        "Add a discarded entry wristband, a water glass, a handbag and minor creasing where someone recently sat.",
        "Add a small tray with glasses, a lipstick beside a phone and a light jacket draped over the back of a chair.",
    ),
    "luxury_brothel": (
        "Add a silk robe draped over a chair, an open cosmetics case, a room-service water glass and lightly creased premium sheets.",
        "Add a folded towel, elegant slippers, a charging phone and a small tray with tea that has already been tasted.",
        "Add a perfume bottle, a half-open jewelry case, a robe on the bed corner and a used water glass on the side table.",
        "Add a handbag near the vanity, carefully arranged cosmetics, one displaced cushion and subtle signs that the suite is regularly occupied.",
    ),
    "adult_club": (
        "Add a leather folio, a partly finished drink, a tailored coat on the chair and a phone beside a discreet membership envelope.",
        "Add a crystal water glass, a perfume atomizer, a closed handbag and one lounge cushion shifted by recent use.",
        "Add a charging phone, a small jewelry case, a silk scarf on the chair arm and a drink coaster with a fresh ring.",
        "Add a private-club envelope, a leather handbag, a half-finished glass and a neatly folded coat showing recent occupancy.",
    ),
}

_PORTRAIT_OCCUPATION_DIRECTIONS = {
    "站街女": (
        "Her presentation is direct, conspicuous and street-facing, with a practiced "
        "inviting expression suited to the nearby street or alley entrance."
    ),
    "发廊妹": (
        "Her styling is inexpensive, direct and deliberately enticing, with the partition "
        "curtain and narrow bed clearly identifying a small-shop advertisement."
    ),
    "妓女": (
        "Her presentation has a practiced client-facing polish and a confident welcoming "
        "expression, clearly identifying her as the principal subject of the advertisement."
    ),
    "冰妹": (
        "Her eyes are slightly glazed and unfocused and her energy is languid, with a "
        "soft vacant allure; the effect should be visible but not grotesque or medically dramatic."
    ),
    "SM妓女": (
        "Faint restraint marks may be visible on her wrists or ankles. Her wardrobe, expression "
        "and styling convey a consensual submissive theme without depicting an explicit act."
    ),
    "性奴": (
        "Her wardrobe, expression and surrounding room convey strict control, compliance and "
        "subordination without depicting an explicit act."
    ),
}

_PORTRAIT_CLOTHING_CATEGORIES_BY_TIER = {
    "street": (
        "street_soliciting", "street_soliciting", "street_soliciting",
        "party_qipao_mini_dresses",
    ),
    "budget_indoor": (
        "budget_privatewear", "budget_privatewear", "camisole_sets", "loungewear",
        "short_nightdress", "massage_privatewear", "after_party_privatewear",
        "submissive_fetishwear", "adult_maid_cosplay",
    ),
    "standard": (
        "short_nightdress", "camisole_sets", "slip_dresses", "loungewear",
        "adult_preppy_cosplay", "adult_nurse_cosplay", "adult_maid_cosplay",
        "party_qipao_mini_dresses", "after_party_privatewear",
        "submissive_fetishwear", "massage_privatewear",
    ),
    "nightlife": (
        "nightlife_glamour", "nightlife_glamour", "party_qipao_mini_dresses",
        "slip_dresses", "adult_preppy_cosplay", "adult_nurse_cosplay",
        "adult_maid_cosplay", "after_party_privatewear", "submissive_fetishwear",
    ),
    "luxury": (
        "luxury_privatewear", "luxury_privatewear", "slip_dresses",
        "short_nightdress", "party_qipao_mini_dresses", "adult_preppy_cosplay",
        "adult_nurse_cosplay", "adult_maid_cosplay", "submissive_fetishwear",
    ),
}

_PORTRAIT_CLOTHING_CATEGORIES_BY_OCCUPATION = {
    "站街女": {"street_soliciting", "party_qipao_mini_dresses", "camisole_sets"},
    "发廊妹": {"budget_privatewear", "camisole_sets", "loungewear", "short_nightdress"},
    "妓女": {
        "street_soliciting", "budget_privatewear", "massage_privatewear", "short_nightdress", "camisole_sets",
        "slip_dresses", "loungewear", "adult_preppy_cosplay", "adult_nurse_cosplay",
        "adult_maid_cosplay", "party_qipao_mini_dresses", "nightlife_glamour",
        "luxury_privatewear",
    },
    "冰妹": {
        "after_party_privatewear", "street_soliciting", "budget_privatewear",
        "camisole_sets", "loungewear", "slip_dresses", "nightlife_glamour",
        "luxury_privatewear",
    },
    "SM妓女": {
        "submissive_fetishwear", "short_nightdress", "slip_dresses",
        "adult_maid_cosplay", "party_qipao_mini_dresses", "luxury_privatewear",
    },
    "性奴": {
        "street_soliciting", "submissive_fetishwear", "budget_privatewear", "short_nightdress",
        "camisole_sets", "loungewear", "adult_maid_cosplay", "luxury_privatewear",
    },
}

_PORTRAIT_SIGNATURE_CLOTHING_CATEGORY = {
    "站街女": "street_soliciting",
    "发廊妹": "budget_privatewear",
    "冰妹": "after_party_privatewear",
    "SM妓女": "submissive_fetishwear",
    "性奴": "submissive_fetishwear",
}

_PORTRAIT_WARDROBE_QUALITY_BY_TIER = {
    "street": (
        "Wardrobe quality: inexpensive, conspicuous and practical for outdoor nightlife work; "
        "it must not look like luxury couture."
    ),
    "budget_indoor": (
        "Wardrobe quality: inexpensive small-shop styling using believable synthetic, "
        "stretch-knit or satin-look materials; neat enough for an advertisement but not luxurious."
    ),
    "standard": (
        "Wardrobe quality: presentable mid-range privatewear, coordinated and commercially "
        "attractive without looking either shabby or elite."
    ),
    "nightlife": (
        "Wardrobe quality: polished club-ready styling with controlled shine and stronger "
        "visual impact suited to KTV or nightclub lighting."
    ),
    "luxury": (
        "Wardrobe quality: premium silk, satin, velvet, fine lace or precise tailoring, "
        "with restrained expensive accessories and no cheap-looking materials."
    ),
}

_NEGATIVE_PORTRAIT_PERSONALITY_TAGS = {
    "色情狂", "暴戾", "控制欲强", "虐待狂", "势利", "善妒", "记仇", "阴险",
    "傲慢", "虚伪", "言而无信", "贪得无厌", "多疑", "残忍", "虚荣拜金",
    "嫉妒成性", "性饥渴", "懦弱自卑", "恶毒腹黑", "欺软怕硬",
}

_PORTRAIT_PERSONALITY_HINTS = {
    "寡言": "a quiet, mysterious and alluring presence",
    "温和": "a soft, gentle and warmly inviting expression",
    "沉稳": "a calm, mature and composed visual presence",
    "谨慎": "a reserved but attentive and intriguing gaze",
    "健谈": "an open, engaging and flirtatious expression",
    "机敏": "sharp, intelligent and captivating eyes",
    "坚韧": "a resilient yet feminine and self-possessed presence",
    "随和": "an easygoing, approachable and naturally inviting expression",
    "节俭": "simple, natural and unshowy presentation",
    "务实": "grounded, confident and believable client-facing charm",
    "慷慨": "a warm, generous and welcoming expression",
    "守信": "a sincere, dependable and softly reassuring look",
}

_PORTRAIT_FACE_COPY_BY_GRADE = {
    "相貌平庸": (
        "Use flattering makeup and careful lighting while keeping her ordinary, recognizable facial geometry.",
        "Present her face with believable makeup and soft light, preserving its unpolished individual character.",
        "Let styling and expression carry the portrait without artificially replacing her natural facial structure.",
    ),
    "相貌普通": (
        "Her face should feel approachable and camera-ready, with makeup refining rather than erasing its individual details.",
        "Use restrained retouching to bring out her eyes and facial contours while keeping a believable everyday face.",
        "Give her face a polished client-facing finish that remains natural and specifically hers.",
    ),
    "长相漂亮": (
        "Her facial appeal should come from the balance of her eyes, lips and facial contours rather than a generic beauty-filter face.",
        "Let the camera favor her specific eye shape and facial lines, giving her a polished but recognizable presence.",
        "Her face should read as vivid and memorable, with professional makeup preserving each distinguishing detail.",
        "Use light and makeup to emphasize her facial harmony while retaining her exact chin, nose and eye structure.",
    ),
    "漂亮得十分惹眼": (
        "Her face has immediate visual impact; center that impact on her particular eyes, contours and distinguishing marks.",
        "Give her a commanding camera presence while preserving the precise geometry that makes her face recognizable.",
        "Her features should hold attention through their sharp individual definition, not through generic smoothing.",
        "Make her facial presence memorable and high-impact, with restrained retouching that keeps every specific feature intact.",
        "Her face should dominate the first impression through confident expression, clear contours and distinctive detail.",
        "Direct the viewer first toward her eyes and facial structure, allowing their unusual combination to define the portrait.",
        "Her camera presence should feel effortless and specific, with makeup supporting rather than standardizing her features.",
        "Let the lighting trace her jaw, nose and eye shape so the strength of her face comes from structure and expression.",
        "Treat her distinguishing marks and facial proportions as valuable identity cues instead of smoothing them away.",
        "Her expression should give the face a vivid focal point while fine skin texture and asymmetry remain believable.",
        "Use controlled contrast to make her particular facial lines read clearly from the first glance.",
        "Give the face editorial clarity and confidence without turning it into a standardized luxury-model face.",
    ),
}

_PORTRAIT_BODY_COPY_BY_GRADE = {
    "身材较差": (
        "Use flattering framing and fitted clothing while preserving every stated measurement.",
        "Keep her true proportions visible through accurate framing and garment fit rather than reshaping her body.",
    ),
    "身材普通": (
        "Show her proportions clearly and naturally, using the outfit and framing to create a balanced silhouette.",
        "Let fitted styling clarify her real waist, bust and hip relationships without exaggerating them.",
        "Use clear full-figure framing that makes her recorded proportions easy to read.",
    ),
    "身材出众": (
        "Make her exact waist, bust and hip relationships a strong part of the composition without altering them.",
        "Use directional light to reveal the distinctive balance of her recorded proportions.",
        "Let her silhouette carry the image, especially the specific transitions between bust, waist and hips.",
        "Frame her figure so its individual proportion pattern remains clear rather than becoming an idealized generic body.",
    ),
    "身材极佳": (
        "Build the composition around her precise silhouette, keeping every stated measurement and proportion visibly credible.",
        "Her figure should provide the portrait's strongest visual rhythm through its exact bust, waist and hip transitions.",
        "Use controlled lighting to make her individual silhouette immediately legible without anatomical exaggeration.",
        "Give her body a confident, sculptural presence while preserving the recorded proportions exactly.",
        "Make the particular balance of her waist and hips central to the image instead of substituting a generic model figure.",
        "Let the garment follow her recorded contours naturally, making the exact waist-to-hip relationship easy to see.",
        "Use three-quarter-body framing to reveal how her height, waist and hip measurements combine into this particular figure.",
        "Keep the silhouette anatomically grounded, drawing attention to proportion rather than exaggerated volume.",
        "Shape the light across her torso and hips so her real measurement differences remain visually consistent.",
        "Show the continuity from shoulders through waist to hips while preserving her individual balance.",
        "The image should make the numerical proportions believable in three dimensions rather than merely decorative data.",
        "Let fabric tension and accurate contour definition communicate her silhouette without digitally narrowing or enlarging any area.",
    ),
}

_PORTRAIT_MARKETING_DIRECTIONS = {
    "站街女": (
        "The result should feel like a bold street-facing advertisement: immediate, legible and grounded in this specific neighborhood.",
        "Frame her as someone who catches a passerby's eye quickly, with direct visual communication rather than polished luxury branding.",
        "Treat the portrait as practical street promotion, using her expression, clothing and setting to make the invitation instantly readable.",
    ),
    "发廊妹": (
        "The result should resemble an informal small-shop advertisement, intimate and direct rather than professionally luxurious.",
        "Make the image feel like locally produced salon promotion, with close physical presence and believable low-budget styling.",
        "Present her as the recognizable face of this small shop through close framing and believable low-budget styling.",
    ),
    "妓女": (
        "Shape the portrait as polished client advertising, balancing professional presentation with a recognizable personal identity.",
        "The image should work as a confident service advertisement built around her particular face, proportions and manner.",
        "Present her with practiced commercial poise, making the photograph persuasive through specificity rather than generic glamour.",
        "Create a composed client-facing portrait whose appeal comes from her individual silhouette, expression and setting.",
    ),
    "冰妹": (
        "Build the advertisement around her languid after-hours presence, keeping the mood alluring but visibly distinct from ordinary glamour.",
        "Let her distant gaze and loosened energy define the photograph, while the venue still reads clearly as a working nightlife space.",
        "Use her hazy, late-night expression as the portrait's identifying hook rather than generic glamour.",
    ),
    "SM妓女": (
        "Center the advertisement on a consensual submissive theme, using wardrobe and small visual cues instead of an explicit scene.",
        "Make the venue, expression and wardrobe details the photograph's defining commercial signals.",
        "The portrait should communicate consensual submissiveness through expression and styling while remaining non-explicit.",
    ),
    "性奴": (
        "Make strict control and compliance the identifying theme of the advertisement, reinforced by the surrounding room.",
        "The portrait's commercial identity should come from a passive, compliant expression rather than conventional glamour alone.",
        "Use a receptive expression and lived-in surroundings to create a clearly subordinate but non-explicit promotional image.",
    ),
}


def _sanitize_portrait_prompt(prompt: str) -> str:
    """Remove high-risk occupation labels while preserving visual direction."""

    prompt = prompt.replace("性奴", "deeply submissive adult worker")
    return re.sub(
        r"\b(?:sexual\s+slave|sex\s+slave)s?\b",
        "deeply submissive adult worker",
        prompt,
        flags=re.IGNORECASE,
    )


def build_character_portrait_prompt(character: dict) -> str:
    age = f"{character['age']} years old"

    def row_value(key, default):
        """Read a candidate field safely, including missing and NULL values."""
        if key not in character.keys():
            return default
        value = character[key]
        return default if value is None or value == "" else value

    occupation = row_value("occupation", "妓女")
    personality_tags = list(row_value("personality_tags", []))
    temperament = row_value("temperament", "温婉感")
    body_grade = row_value("body_grade", "身材出众")
    face_grade = row_value("attractiveness_grade", "长相漂亮")
    hygiene = row_value("hygiene", "卫生良好")
    presentation = row_value("presentation", "朴素整洁")
    portrait_descriptions = build_portrait_descriptions(character)
    CLOTHING_POOL_BY_TYPE = {
        # 13
        "short_nightdress": [
            "a very short black satin nightdress with thin spaghetti straps, a softly fitted waist, and a glossy smooth finish",
            "a very short ivory silk nightdress with delicate lace trim, narrow shoulder straps, and a fluid drape",
            "a short wine-red satin nightdress with a body-skimming cut, subtle lace edging, and a soft sheen",
            "a short blush-pink silk nightdress with a lightly ruched bust, thin straps, and a smooth luxurious texture",
            "a short midnight-blue satin nightdress with a sleek fitted silhouette and a polished reflective surface",
            "a short emerald-green silk nightdress with fine lace trim, a fitted waistline, and a soft flowing hem",
            "a short champagne-colored satin nightdress with a lightly shimmering finish and a close feminine fit",
            "a short dark-purple silk nightdress with a refined glossy texture and a flattering body-hugging cut",
            "a short ruby-red satin nightdress with black lace accents, a fitted bust, and a smooth cool-touch surface",
            "a short charcoal-gray silk nightdress with minimal thin straps and a sleek, clean-lined silhouette",
            "a fitted cream satin nightdress with soft lace edging and a very short, curve-flattering hem",
            "a short rose-gold silk nightdress with a softly draped neckline and a luminous smooth texture",
            "a short deep-teal satin nightdress with thin straps, a neat fitted waist, and a glossy elegant finish",
        ],

        # 13
        "camisole_sets": [
            "a tight black satin camisole with thin straps paired with a very short matching satin skirt",
            "a white silk camisole with narrow straps paired with fitted black short shorts in smooth satin",
            "a red satin camisole with lace-trimmed neckline paired with a very short fitted mini skirt",
            "a pale-pink silk camisole with delicate lace edging paired with fitted satin shorts",
            "a deep-blue satin camisole with a sleek glossy texture paired with a high-waisted fitted mini skirt",
            "a burgundy silk camisole with black lace trim paired with very short smooth shorts",
            "a champagne satin camisole with a body-hugging fit paired with a short fitted skirt",
            "a dark-green silk camisole with narrow straps paired with fitted black satin shorts",
            "a lavender satin camisole with subtle sheen paired with a close-fitting mini skirt",
            "a cream silk camisole with fine lace trim paired with tight soft modal shorts",
            "a black satin camisole with a fitted waist paired with short glossy lounge shorts",
            "a rose-red silk camisole with a low but fully covered neckline paired with a short satin wrap skirt",
            "a smoky-purple satin camisole with a sleek finish paired with fitted matching short shorts",
        ],

        # 13
        "slip_dresses": [
            "a tight black silk slip dress with spaghetti straps, a sleek body-skimming cut, and a glossy finish",
            "a fitted crimson satin slip dress with thin straps and a smooth cool-touch texture",
            "a fitted ivory silk slip dress with delicate lace edging and a short fluid hem",
            "a tight navy-blue satin slip dress shaped closely around the waist and hips with a reflective sheen",
            "a fitted champagne silk slip dress with a smooth drape and elegant body-hugging silhouette",
            "a rose-pink satin slip dress with lace trim and a sleek polished finish",
            "a fitted dark-purple silk slip dress with narrow straps and subtle sheen",
            "a sleek emerald satin slip dress with a close body-hugging cut and a glossy surface",
            "a fitted silver-gray silk slip dress with a cool smooth texture and a clean fitted shape",
            "a black satin slip dress with a softly draped neckline and a short elegant hem",
            "a deep-burgundy silk slip dress with a lustrous finish and fitted waistline",
            "an ivory satin slip dress with soft lace panels and a short, flattering silhouette",
            "a midnight-teal silk slip dress with a polished sheen and a close feminine fit",
        ],

        # 13
        "loungewear": [
            "a fitted black ribbed-knit lounge dress with thin straps, a very short hem, and a stretchy body-hugging texture",
            "a soft gray modal lounge dress with a close fit, wide neckline, and a smooth lightweight feel",
            "a fitted cream knit mini dress with long sleeves and a fine soft-touch fabric",
            "a tight burgundy ribbed lounge dress with a modest neckline, short hem, and flexible snug fit",
            "a fitted dusty-pink lounge set with a cropped modal camisole and matching high-waisted shorts",
            "a fitted black lounge set with a thin-strapped top and very short stretch-knit shorts",
            "a soft white lounge top in smooth jersey paired with satin-trimmed short shorts",
            "a dark-red fitted indoor set with a sleeveless knit top and sleek close-fitting shorts",
            "a pale-beige body-hugging lounge dress in soft brushed knit with a gentle matte finish",
            "a fitted mocha-brown knit lounge dress with long sleeves and a short silhouette",
            "a cream ribbed-knit camisole dress with narrow straps and a soft clingy texture",
            "a charcoal modal lounge set with a fitted sleeveless top and short close-fitting bottoms",
            "a blush-pink stretch-knit mini lounge dress with a smooth soft-touch finish and flattering fit",
        ],

        # 12
        "adult_preppy_cosplay": [
            "an adult preppy cosplay outfit with a fitted white cotton-poplin blouse, dark pleated mini skirt, and loosened necktie",
            "an adult preppy-inspired outfit with a pale-blue fitted shirt in crisp poplin, a charcoal pleated mini skirt, and knee-high socks",
            "an adult preppy cosplay outfit with a fitted cream blouse, burgundy ribbon tie, and a dark checked mini skirt in woven twill",
            "an adult preppy-inspired outfit with a fitted white blouse in smooth cotton blend, a navy pleated mini skirt, and dark thigh-high stockings",
            "an adult preppy cosplay look with a fitted white shirt, black ribbon tie, and a wine-red plaid mini skirt in brushed fabric",
            "an adult preppy-inspired outfit with a fitted short-sleeved blouse in soft cotton blend and a dark-gray pleated mini skirt",
            "an adult preppy cosplay outfit with a fitted ivory blouse, slim black tie, and a high-waisted navy mini skirt in structured twill",
            "an adult preppy-inspired outfit with a pale-pink fitted blouse, a charcoal plaid mini skirt, and dark over-the-knee socks",
            "an adult preppy cosplay outfit with a fitted white blouse, satin neck ribbon, and a short green plaid pleated skirt",
            "an adult preppy-inspired outfit with a light-gray blouse in soft woven fabric, a fitted black mini skirt, and a loosened tie",
            "an adult preppy cosplay look with a fitted cream blouse, navy bow tie, and a burgundy pleated mini skirt in crisp woven fabric",
            "an adult preppy-inspired outfit with a white fitted blouse, dark checked pleated skirt, and a slightly undone collar for a teasing finish",
        ],

        # 12
        "adult_nurse_cosplay": [
            "a fitted adult nurse cosplay dress in white satin with pale-pink trim, a short hem, and a matching nurse cap",
            "a fitted adult nurse cosplay dress in white stretch fabric with red trim, a short skirt, and a small structured cap",
            "a pale-pink adult nurse cosplay mini dress in smooth satin with white trim and a fitted waist",
            "a sleek white adult nurse cosplay outfit with blue accents, a short fitted skirt, and a matching cap",
            "a black-and-white adult nurse-inspired cosplay dress in stretch satin with a close feminine fit",
            "a light-blue adult nurse cosplay mini dress in glossy smooth fabric with white edging and a fitted waistline",
            "a fitted ivory adult nurse cosplay dress with red piping, short sleeves, and a gently structured silhouette",
            "a white satin nurse-inspired mini dress with soft pink lace accents and a neat fitted waist",
            "a fitted white nurse cosplay mini dress in matte stretch fabric with burgundy trim and a short hem",
            "a pale-lilac adult nurse cosplay dress in smooth satin with white cuffs and a matching cap",
            "a fitted dark-red and white nurse-inspired mini dress in stretch fabric with a polished theatrical look",
            "a white adult nurse cosplay dress with black trim in sleek smooth fabric and a short body-flattering skirt",
        ],

        # 12
        "adult_maid_cosplay": [
            "a fitted black-and-white adult maid cosplay mini dress in satin with lace trim and a small apron",
            "a dark-red and black adult maid-inspired mini dress in smooth satin with fitted waist and lace edging",
            "a black satin adult maid cosplay dress with white lace accents and a very short flared skirt",
            "a dark-purple adult maid-inspired outfit with black lace trim and a fitted short satin skirt",
            "a fitted navy-and-white adult maid cosplay dress in smooth woven fabric with a short apron",
            "a black-and-cream adult maid-inspired mini dress in satin with puff sleeves and lace edges",
            "a fitted burgundy maid cosplay outfit with white lace trim, glossy finish, and a short gathered skirt",
            "a dark emerald adult maid-inspired mini dress with white apron details and a neat fitted waist",
            "a fitted charcoal-black maid cosplay mini dress with satin panels, lace trim, and a short skirt",
            "a black-and-pink adult maid-inspired outfit in satin with delicate lace cuffs and a fitted bodice",
            "a deep-plum maid cosplay dress with white apron contrast and a short flirty hem in smooth satin",
            "a fitted black maid-inspired mini dress with glossy satin texture, lace neckline, and a structured small apron",
        ],

        # 12
        "party_qipao_mini_dresses": [
            "a very short fitted black qipao-inspired satin dress with red piping and an elegant high collar",
            "a short fitted crimson qipao-inspired dress in silk-blend fabric with black trim and a sleek structured silhouette",
            "a short dark-blue satin qipao-inspired dress with subtle floral detailing and a fitted waistline",
            "a short emerald-green qipao-inspired dress in smooth silk-like fabric with gold piping and a body-skimming fit",
            "a fitted ivory qipao-inspired mini dress with delicate red floral accents in jacquard fabric",
            "a tight black off-shoulder mini dress in stretch velvet with a sleek fitted silhouette",
            "a fitted metallic-silver party mini dress with thin straps and a subtle reflective satin finish",
            "a tight deep-red party mini dress in stretch crepe with a clean body-hugging cut",
            "a fitted royal-blue mini dress in smooth satin with a low but fully covered neckline and very short hem",
            "a tight dark-green velvet mini dress with thin straps and a luxurious rich texture",
            "a fitted black velvet mini dress with an off-shoulder neckline and a short elegant hem",
            "a fitted champagne-colored party mini dress in softly shimmering satin with thin straps and a polished glamorous finish",
        ],

        # Low-cost street advertising: practical enough to stand or walk outdoors,
        # but conspicuous in a nightlife district.
        "street_soliciting": [
            "a tight red stretch-knit mini dress under a cropped black faux-leather jacket, with inexpensive ankle boots",
            "a fitted black halter mini dress with a short denim jacket and glossy knee-high boots",
            "a bright magenta camisole top with a black vinyl-look mini skirt and a small shoulder bag",
            "a white fitted tank top with very short dark denim shorts, sheer tights and a cropped jacket",
            "a leopard-print stretch mini dress with a narrow black belt and inexpensive high-heeled sandals",
            "a shiny cobalt-blue tube mini dress with a cropped faux-fur jacket and dark ankle boots",
            "a fitted burgundy wrap mini dress with sheer black stockings and a compact street-ready handbag",
            "a black lace-trim camisole with a red plaid mini skirt, dark tights and a worn cropped jacket",
            "a silver stretch camisole with a tight black mini skirt and a lightweight jacket draped over her shoulders",
            "a fitted purple knit mini dress with over-the-knee socks and practical platform shoes",
            "a glossy black sleeveless mini dress with a bright red cardigan and a small zippered purse",
            "a rose-pink cropped top with fitted black shorts, sheer stockings and a cheap faux-leather jacket",
            "a deep-green bodycon mini dress with a short black coat left open for the photograph",
            "a cream ribbed camisole dress with a denim jacket, patterned tights and compact ankle boots",
            "a scarlet off-shoulder stretch mini dress with a narrow belt and an inexpensive metallic clutch",
            "a black fitted top with a glossy wine-red mini skirt and a cropped hooded jacket",
        ],

        # Hair salons and other very small low-cost indoor venues.
        "budget_privatewear": [
            "a cheap-looking pink satin camisole with matching very short lounge shorts and simple house slippers",
            "a fitted black stretch-knit mini dress with slightly worn fabric and inexpensive lace trim",
            "a bright red polyester slip dress with narrow straps, a short hem and a modest glossy finish",
            "a white ribbed tank top with tight pale-pink shorts and simple foam house sandals",
            "a purple lace-trim camisole with a black stretch mini skirt and inexpensive jewelry",
            "a floral-print wrap mini dress in thin synthetic fabric with a close fitted waist",
            "a turquoise satin-look nightdress with cheap lace edging and a very short hem",
            "a fitted gray lounge camisole with black modal shorts and a lightweight robe left open",
            "a black mesh-panel mini dress with opaque lining, simple straps and a low-cost polished finish",
            "a wine-red stretch camisole dress with a tiny decorative bow and simple house slippers",
            "a pale-yellow fitted tank dress in inexpensive jersey with a short, clingy silhouette",
            "a rose-red satin-look top with tight black shorts and an inexpensive lace cover-up",
            "a white buttoned salon smock worn loosely over a fitted black camisole and very short shorts",
            "a glossy lavender mini nightdress with narrow straps and slightly uneven cheap lace trim",
            "a fitted leopard-print camisole with plain black lounge shorts and simple plastic bangles",
            "a coral-pink stretch mini dress with a cropped cardigan and inexpensive heeled slippers",
        ],

        # Massage-room advertising remains suggestive but recognizably tied to that venue.
        "massage_privatewear": [
            "a fitted pale-pink wrap mini dress in smooth stretch fabric with clean white piping",
            "a short white spa tunic with a cinched waist, side slit and fitted pale-gray shorts underneath",
            "a black satin wrap dress with three-quarter sleeves, a short hem and a narrow waist tie",
            "a light-blue fitted massage uniform dress with white trim and a short practical skirt",
            "a cream sleeveless wrap top paired with a close-fitting dark-brown mini skirt",
            "a burgundy short robe dress in soft satin with a secure tied waist and polished finish",
            "a fitted lavender spa tunic with a mandarin collar, short hem and subtle satin sheen",
            "a white ribbed camisole paired with fitted black massage shorts and a lightweight wrap robe",
            "a dark-green satin wrap mini dress with clean piping and a neatly tied waist",
            "a blush-pink fitted tunic dress with a small side slit and smooth professional fabric",
            "a champagne-colored short robe with a fitted inner slip and understated lace edging",
            "a charcoal sleeveless massage dress with a close waist, short hem and pale-pink trim",
            "a fitted ivory wrap blouse paired with a sleek wine-red mini skirt",
            "a navy satin short robe dress with white piping and a softly polished surface",
        ],

        # Nightclubs and business KTVs use bolder stage-aware styling.
        "nightlife_glamour": [
            "a fitted black sequin mini dress with narrow straps and controlled reflective sparkle",
            "a deep-red velvet bodycon mini dress with an off-shoulder neckline and gold-toned earrings",
            "a metallic cobalt-blue mini dress with a sleek asymmetric neckline and nightclub sheen",
            "a champagne sequin camisole dress with a fitted waist and a short polished silhouette",
            "a glossy black satin bustier-style top with a fitted burgundy mini skirt and statement earrings",
            "a dark-purple velvet halter mini dress with a close waist and restrained crystal accents",
            "a silver-gray fitted cocktail mini dress with a draped neckline and subtle reflective texture",
            "a ruby-red satin mini dress with one shoulder, a sculpted waist and elegant nightclub styling",
            "a black fitted qipao-inspired mini dress with gold piping and a dark floral jacquard texture",
            "an emerald-green velvet mini dress with thin straps and a refined body-skimming shape",
            "a midnight-blue satin corset-style top with a fitted black mini skirt and polished jewelry",
            "a rose-gold shimmer mini dress with a clean fitted silhouette and delicate drop earrings",
            "a black-and-silver fitted stage mini dress with opaque panels and tasteful geometric sparkle",
            "a wine-red lace-overlay mini dress with full lining, narrow straps and a glamorous finish",
            "a pearl-white satin cocktail mini dress with a softly draped neckline and crystal earrings",
            "a dark-teal fitted mini dress with subtle sequins, a sleek waist and a polished club-ready finish",
        ],

        # High-end brothels and private clubs favor expensive materials and controlled luxury.
        "luxury_privatewear": [
            "an ivory silk slip dress beneath an open matching floor-length robe with delicate hand-finished lace",
            "a black silk camisole and fitted short skirt beneath a sheer-trimmed satin dressing gown",
            "a champagne silk mini dress with a softly draped neckline, refined tailoring and pearl earrings",
            "a deep-burgundy velvet mini dress with a sculpted waist and understated fine jewelry",
            "an emerald silk qipao-inspired mini dress with restrained gold piping and fine jacquard texture",
            "a midnight-blue satin slip dress with a tailored waist and a lightweight silk robe draped behind her",
            "a blush-pink silk camisole set with a fitted wrap skirt and delicate lace finishing",
            "a fitted black velvet off-shoulder mini dress with a clean silhouette and discreet diamond-like studs",
            "a silver-gray silk halter mini dress with precise tailoring and a luminous, expensive finish",
            "a cream satin corset-seamed mini dress with full coverage and an elegant matching robe",
            "a ruby-red silk slip dress with black French-style lace trim and refined evening jewelry",
            "a dark-plum satin wrap mini dress with a hand-finished waist tie and polished luxury texture",
            "a pearl-white fitted qipao-inspired mini dress with subtle embroidery and a structured high collar",
            "a rose-gold silk mini dress with a fluid drape, narrow straps and a matching sheer-edged robe",
            "a black satin camisole with tailored high-waisted shorts and a long lightweight silk dressing gown",
            "a deep-teal velvet mini dress with a square neckline, shaped waist and restrained gemstone accents",
        ],

        # Non-explicit submissive styling shared by SM workers and sex slaves.
        "submissive_fetishwear": [
            "a fitted black satin mini dress with a slim choker, soft decorative wrist cuffs and opaque lace panels",
            "a deep-red velvet camisole dress with a narrow collar and loose decorative ribbon restraints at the wrists",
            "a black faux-leather corset-seamed mini dress with full opaque coverage and a soft collar",
            "a white satin slip dress with black piping, a slim neck ribbon and soft detachable wrist cuffs",
            "a dark-purple lace-overlay mini dress with full lining, a narrow choker and restrained harness-inspired seams",
            "a black-and-burgundy maid-inspired mini dress with a fitted waist, slim collar and decorative cuffs",
            "a fitted charcoal satin camisole with a short black skirt, thigh-high stockings and a simple collar",
            "a pale-pink silk mini dress with a black neck ribbon and soft satin wrist ties left visibly loose",
            "a black velvet halter mini dress with an understated collar ring and opaque structured panels",
            "a wine-red satin wrap dress with a slim choker and delicate decorative ankle ribbons",
            "an ivory silk nightdress with a fitted black waist belt, soft cuffs and a modest high-cut lace neckline",
            "a dark-blue satin mini dress with corset-inspired seams, opaque fabric and a narrow velvet collar",
            "a black fitted lounge set with a satin camisole, short skirt and soft decorative wrist bands",
            "a crimson silk slip dress with full lining, a discreet collar and loose black ribbon accents",
            "a fitted black qipao-inspired mini dress with red piping, a slim collar and decorative wrist cuffs",
            "a charcoal-gray velvet mini dress with a structured waist, opaque lace sleeves and a simple neck band",
        ],

        # Ice-girl styling: recognizably after-hours and slightly disordered, not filthy.
        "after_party_privatewear": [
            "a silver camisole mini dress with one strap slightly displaced and a cropped jacket loosely draped behind her",
            "a black satin slip dress with a softly rumpled hem and an open lightweight cardigan",
            "a rose-pink fitted lounge top with very short satin shorts and a loose oversized shirt",
            "a metallic-blue party mini dress with slightly tousled styling and one earring removed",
            "a white ribbed camisole with a fitted black mini skirt and a loose jacket slipping from one shoulder",
            "a burgundy silk-look nightdress with a lightly rumpled surface and a thin robe left open",
            "a dark-purple velvet mini dress with subtly disordered hair styling and an unfastened decorative bracelet",
            "a pale-gray fitted tank dress with an oversized black cardigan and a soft lived-in texture",
            "a red satin camisole paired with tight dark shorts and a lightweight shirt tied loosely at the waist",
            "a black sequin mini dress with restrained sparkle and a casually draped lounge robe",
            "a champagne slip dress with a slightly creased finish and a loose cardigan falling behind her arms",
            "a teal stretch-knit mini dress with a partly slipped jacket and simple after-hours jewelry",
            "a fitted black camisole with a wine-red wrap mini skirt and a loosely worn hooded jacket",
            "a dusty-pink lounge mini dress with a rumpled lightweight robe and minimal accessories",
        ],
    }
    organization_template = str(
        row_value("organization_template", "")
        or row_value("organization_type", "")
        or _PORTRAIT_DEFAULT_TEMPLATE_BY_OCCUPATION.get(occupation, "ordinary_brothel")
    )
    venue_tier = _PORTRAIT_VENUE_TIER_BY_TEMPLATE.get(organization_template, "standard")
    tier_categories = _PORTRAIT_CLOTHING_CATEGORIES_BY_TIER[venue_tier]
    occupation_categories = _PORTRAIT_CLOTHING_CATEGORIES_BY_OCCUPATION.get(
        occupation,
        set(CLOTHING_POOL_BY_TYPE),
    )
    eligible_categories = [
        category for category in tier_categories if category in occupation_categories
    ]
    if not eligible_categories:
        eligible_categories = list(tier_categories)
    signature_category = _PORTRAIT_SIGNATURE_CLOTHING_CATEGORY.get(occupation)
    if signature_category in eligible_categories:
        eligible_categories.extend([signature_category] * 3)
    category = random.choice(eligible_categories)
    clothing_text = random.choice(CLOTHING_POOL_BY_TYPE[category])
    wardrobe_quality_text = _PORTRAIT_WARDROBE_QUALITY_BY_TIER[venue_tier]

    # Ratings control varied photographic direction rather than repeating a
    # single generic claim that every worker is "exceptionally attractive".
    face_text = _portrait_stable_choice(
        character,
        "face-copy",
        _PORTRAIT_FACE_COPY_BY_GRADE.get(
            face_grade,
            _PORTRAIT_FACE_COPY_BY_GRADE["长相漂亮"],
        ),
    )
    body_text = _portrait_stable_choice(
        character,
        "body-copy",
        _PORTRAIT_BODY_COPY_BY_GRADE.get(
            body_grade,
            _PORTRAIT_BODY_COPY_BY_GRADE["身材出众"],
        ),
    )
    hygiene_map = {
        "肮脏恶臭": "freshly washed and made acceptably clean and camera-ready for this photograph",
        "污秽不堪": "recently cleaned and groomed into a believable camera-ready condition for this photograph",
        "卫生较差": "freshly washed, clean and naturally groomed for the photograph",
        "卫生良好": "clean, fresh and meticulously groomed",
    }
    presentation_map = {
        "疲惫凌乱": "slightly tired and intentionally tousled, but clean and deliberately arranged for the camera",
        "随意自然": "casual, relaxed and naturally arranged for the camera",
        "朴素整洁": "simple, neat and understated",
        "干净利落": "clean, crisp and efficiently styled",
        "精心打理": "carefully styled and polished",
        "精致讲究": "refined and meticulous within the believable wardrobe and budget of her workplace",
    }
    hygiene_text = hygiene_map.get(hygiene, "clean, fresh and professionally groomed")
    presentation_text = presentation_map.get(
        presentation,
        "realistically styled and prepared for the camera",
    )

    # Venue background and occupation direction are separate so both dimensions
    # remain visible without issuing contradictory setting instructions.
    background_text = _PORTRAIT_VENUE_BACKGROUNDS.get(
        organization_template,
        _PORTRAIT_VENUE_BACKGROUNDS["ordinary_brothel"],
    )
    lived_in_text = random.choice(
        _PORTRAIT_VENUE_LIVED_IN_DETAILS.get(
            organization_template,
            _PORTRAIT_VENUE_LIVED_IN_DETAILS["ordinary_brothel"],
        )
    )
    occupation_text = _PORTRAIT_OCCUPATION_DIRECTIONS.get(
        occupation,
        _PORTRAIT_OCCUPATION_DIRECTIONS["妓女"],
    )
    marketing_text = _portrait_stable_choice(
        character,
        "marketing-copy",
        _PORTRAIT_MARKETING_DIRECTIONS.get(
            occupation,
            _PORTRAIT_MARKETING_DIRECTIONS["妓女"],
        ),
    )

    # ---------- 性格 → 宣传照表情 ----------
    retained_personality_tags = [
        tag
        for tag in personality_tags
        if tag not in _NEGATIVE_PORTRAIT_PERSONALITY_TAGS
        and tag in _PORTRAIT_PERSONALITY_HINTS
    ]
    if retained_personality_tags:
        personality_text = (
            "Her facial expression and overall visual presence should also convey: "
            f"{', '.join(_PORTRAIT_PERSONALITY_HINTS[tag] for tag in retained_personality_tags)}. "
        )
    else:
        personality_text = ""

    # ---------- 风格 ----------
    style_map = {
        "御姐感": "mature, assertive and highly seductive",
        "甜妹感": "sweet, youthful and inviting",
        "清冷感": "cool, aloof and powerfully alluring",
        "娇媚感": "charming, glamorous and openly flirtatious",
        "成熟风韵": "mature, elegant and sensually self-possessed",
        "冷锐感": "cool, sharp and commanding",
        "软萌感": "soft, approachable and playfully charming",
        "温婉感": "gentle, graceful and warmly inviting",
    }
    style_text = style_map.get(temperament, "a distinctive feminine style")

    prompt = (
        f"Create a realistic commercial portrait of one adult Chinese sex worker, {age}, intended for client-facing advertising. "
        "Preserve the following concrete identity details rather than replacing them with a generic model. "
        f"Hair: {portrait_descriptions['hair']}. Skin: {portrait_descriptions['skin']}. "
        f"Her presentation is {presentation_text}. "
        f"Face: {portrait_descriptions['face']}. "
        f"Body measurements: {portrait_descriptions['body_metrics']}. "
        f"Body shape: {portrait_descriptions['body_shape']}. "
        f"{face_text} {body_text} "
        f"For this promotional shoot she is {hygiene_text}. "
        f"Her personal style is {style_text}. "
        f"{personality_text}"
        f"{background_text} "
        f"{lived_in_text} "
        f"{occupation_text} "
        f"{marketing_text} "
        "Use flattering but realistic grooming, lighting and framing without changing her stated face, measurements or body proportions. "
        "Keep the mood intimate, suggestive and clearly intended for adult clients while remaining non-explicit. "
        "Composition: three-quarter-body to full-body framing, with her figure clearly visible. "
        f"Clothing: she is wearing {clothing_text}. "
        f"{wardrobe_quality_text} "
        "The styling should remain fully non-nude and use the garment's cut and drape to clarify her recorded silhouette. "
        "Make her look distinct and specific to the described body type, hair, face and expression. "
        "Natural skin texture, realistic flattering lighting, one person only, no text, no watermark, "
        "no nudity, no explicit sexual content, no pornographic acts, no heavy beauty filter, no generic identical face."
    )
    return _sanitize_portrait_prompt(prompt)
