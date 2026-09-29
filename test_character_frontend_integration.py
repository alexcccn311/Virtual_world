"""Regression coverage for the reference-city character picker."""
from __future__ import annotations

import base64
import json
import random
import re
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from customer_characters import (  # noqa: E402
    available_character_worlds,
    build_character_portrait_prompt,
    change_player_organization,
    confirm_city_character,
    create_and_save_customer_character,
    delete_character_world,
    delete_customer_character,
    excluded_source_ids_for_world,
    load_customer_characters,
    update_character_world_time,
    update_player_organization,
)
from daily_work_wallet import (  # noqa: E402
    _character_preview_heading,
    _current_character_finances,
    _money,
)
from world_generation import config as world_config  # noqa: E402
from world_generation.services.character_catalog import (  # noqa: E402
    discover_character_worlds,
    draw_world_character,
)
from world_generation.services.character_description import (  # noqa: E402
    _build_card_body_copy,
    _build_card_invitation_copy,
    build_character_card_summary,
    build_character_portrait_prompt as service_portrait_prompt,
    build_portrait_descriptions,
)


def _sample_payload() -> dict:
    return {
        "id": "CUS-00000042",
        "name": "测试角色",
        "nickname": "小测",
        "district": "中心区",
        "street": "测试街",
        "occupation": "妓女",
        "title": "妓女",
        "education": "高中",
        "skills": {"接吻技巧": "专业", "阴道交": "专业"},
        "sex": "female",
        "age": 24,
        "hair": "乌黑亮丽的长发",
        "skin_quality": "细腻光滑、富有自然光泽的肌肤",
        "income": 12000,
        "cash": 8000,
        "other_assets": 30000,
        "debt": 10000,
        "net_assets": 28000,
        "height": 166,
        "bmi": 21.0,
        "weight": 57.9,
        "bust": 88.0,
        "waist": 64.0,
        "hips": 91.0,
        "personality_tags": ["温和", "务实"],
        "sexual_preferences": None,
        "attraction_preferences": None,
        "hobbies": ["阅读", "电影"],
        "hygiene": "卫生良好",
        "presentation": "精心打理",
        "feature": "笑起来有酒窝",
        "face_shape": "自然柔和的鹅蛋脸",
        "eyes": "明亮的桃花眼",
        "eyebrows": "自然舒展的眉形",
        "nose": "鼻梁自然挺直",
        "lips": "唇形柔和",
        "face_score": 82.0,
        "body_score": 86.0,
        "appearance_score": 83.8,
        "cup_size": "C",
        "temperament": "温婉感",
        "relation": {"superior_id": None},
        "organization_id": "ORG-00000007",
        "organization_name": "测试会馆",
    }


def _build_fixture_database(path: Path) -> None:
    payload = _sample_payload()
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            """
            CREATE TABLE cities (
                city_id TEXT PRIMARY KEY,
                city_template TEXT NOT NULL,
                status TEXT NOT NULL
            );
            INSERT INTO cities VALUES (
                'CITY-00000001', '测试世界', 'complete'
            );
            CREATE TABLE organizations (
                organization_id TEXT PRIMARY KEY,
                city_id TEXT NOT NULL,
                template_name TEXT NOT NULL,
                name TEXT NOT NULL
            );
            INSERT INTO organizations VALUES
                ('ORG-00000007', 'CITY-00000001', 'luxury_brothel', '测试会馆'),
                ('ORG-NEW', 'CITY-00000001', 'ordinary_brothel', '新组织'),
                ('ORG-OTHER', 'CITY-00000001', 'adult_club', '其他组织');
            CREATE TABLE characters (
                character_id TEXT PRIMARY KEY,
                city_id TEXT NOT NULL,
                organization_id TEXT NOT NULL,
                organization_name TEXT NOT NULL,
                district_name TEXT NOT NULL,
                street_name TEXT NOT NULL,
                name TEXT NOT NULL,
                occupation TEXT NOT NULL,
                title TEXT NOT NULL,
                data_json TEXT NOT NULL
            );
            CREATE INDEX idx_characters_occupation ON characters(occupation);
            """
        )
        connection.execute(
            """
            INSERT INTO characters VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["id"],
                "CITY-00000001",
                payload["organization_id"],
                payload["organization_name"],
                payload["district"],
                payload["street"],
                payload["name"],
                payload["occupation"],
                payload["title"],
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        connection.commit()


class CharacterCatalogIntegrationTests(unittest.TestCase):
    def test_new_schema_is_normalised_for_the_frontend(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "city.sqlite3"
            _build_fixture_database(database)
            candidate = draw_world_character(
                "妓女", database=database, rng=random.Random(1)
            )

        self.assertEqual(candidate["source_character_id"], "CUS-00000042")
        self.assertEqual(candidate["source_world_id"], "city.sqlite3")
        self.assertEqual(candidate["source_world_name"], "city")
        self.assertEqual(candidate["source_key"], "city.sqlite3:CUS-00000042")
        self.assertEqual(candidate["organization_template"], "luxury_brothel")
        self.assertEqual(candidate["profile"]["世界"], "city")
        self.assertEqual(candidate["profile"]["所在街道"], "测试街")
        self.assertIn("鹅蛋脸", candidate["profile"]["面部特征"])
        self.assertIn("身高166cm", candidate["profile"]["身材数据"])
        self.assertEqual(candidate["profile"]["净资产"], "¥28,000")
        self.assertEqual(candidate["summary"], build_character_card_summary(candidate))
        self.assertNotIn(candidate["name"], candidate["summary"])
        self.assertNotIn(candidate["nickname"], candidate["summary"])
        self.assertTrue(candidate["summary"].startswith("我今年24岁"))
        self.assertIn("三围是88、64、91厘米", candidate["summary"])
        self.assertIn("安静地读书，也喜欢看电影", candidate["summary"])
        self.assertIn("到了床上，我最会用柔软的身子紧紧缠住你", candidate["summary"])
        self.assertTrue(candidate["summary"].endswith(_build_card_invitation_copy(candidate)))
        self.assertNotIn("温婉感", candidate["summary"])
        self.assertNotIn("风格", candidate["summary"])
        self.assertNotIn("我擅长", candidate["summary"])
        self.assertNotIn("\n", candidate["summary"])

    def test_card_summary_filters_negative_hobbies_but_keeps_mahjong(self) -> None:
        character = _sample_payload()
        character["hobbies"] = ["吸毒", "线上博彩", "麻将"]

        summary = build_character_card_summary(character)

        self.assertNotIn("吸毒", summary)
        self.assertNotIn("线上博彩", summary)
        self.assertIn("和朋友打几圈麻将", summary)

    def test_card_summary_omits_hobbies_when_none_are_suitable(self) -> None:
        character = _sample_payload()
        character["hobbies"] = ["吸毒", "百家乐"]

        summary = build_character_card_summary(character)

        self.assertNotIn("不撩人的时候", summary)

    def test_card_summary_does_not_repeat_equivalent_music_hobbies(self) -> None:
        character = _sample_payload()
        character["hobbies"] = ["嘻哈", "听音乐"]

        summary = build_character_card_summary(character)

        self.assertIn("听嘻哈音乐", summary)
        self.assertNotIn("也喜欢听音乐", summary)

    def test_card_summary_only_promotes_the_highest_advanced_sex_skill(self) -> None:
        character = _sample_payload()
        character["skills"] = {
            "接吻技巧": "专业",
            "口交": "精通",
            "深喉": "大师",
            "职业边界管理": "宗师",
        }

        summary = build_character_card_summary(character)

        self.assertIn("真正含住时，会让你知道什么叫又深又尽兴", summary)
        self.assertNotIn("低下头的时候", summary)
        self.assertNotIn("嘴唇不只适合看", summary)
        self.assertNotIn("深喉", summary)
        self.assertNotIn("大师", summary)

    def test_card_summary_naturally_includes_each_occupation_identity(self) -> None:
        expected_copy = {
            "站街女": "就在街边等客",
            "妓女": "接客这碗饭",
            "发廊妹": "洗头按摩只是开场",
            "冰妹": "瘾和欠下的债把我牢牢拴在场子里",
            "SM妓女": "被送到客人手里受调教的母狗",
            "性奴": "早被调教成只会服从和取悦主人的性奴",
        }
        for occupation, phrase in expected_copy.items():
            with self.subTest(occupation=occupation):
                character = _sample_payload()
                character["occupation"] = occupation
                character["skills"] = {}
                summary = build_character_card_summary(character)
                self.assertIn(phrase, summary)
                self.assertNotIn(f"职业：{occupation}", summary)

    def test_controlled_occupations_use_progressively_degrading_sales_copy(self) -> None:
        expected_markers = {
            "冰妹": ("冰", "瘾"),
            "SM妓女": ("母狗", "主人", "项圈", " M"),
            "性奴": ("性玩具", "性奴", "身体", "东西"),
        }
        for occupation, markers in expected_markers.items():
            with self.subTest(occupation=occupation):
                character = _sample_payload()
                character["occupation"] = occupation
                character["skills"] = {}
                invitation = _build_card_invitation_copy(character)
                self.assertTrue(any(marker in invitation for marker in markers))

    def test_each_occupation_has_many_stable_invitation_endings(self) -> None:
        occupations = ("站街女", "妓女", "发廊妹", "冰妹", "SM妓女", "性奴")
        for occupation in occupations:
            with self.subTest(occupation=occupation):
                invitations = {
                    _build_card_invitation_copy(
                        {
                            **_sample_payload(),
                            "id": f"CUS-{number:08d}",
                            "occupation": occupation,
                        }
                    )
                    for number in range(1, 129)
                }
                self.assertGreaterEqual(len(invitations), 12)

                character = {
                    **_sample_payload(),
                    "id": "CUS-00999999",
                    "occupation": occupation,
                }
                self.assertEqual(
                    _build_card_invitation_copy(character),
                    _build_card_invitation_copy(character),
                )

    def test_high_score_body_copy_is_varied_and_stable(self) -> None:
        body_copies = {
            _build_card_body_copy(
                {**_sample_payload(), "id": f"CUS-{number:08d}"},
                90.0,
            )
            for number in range(1, 129)
        }

        self.assertGreaterEqual(len(body_copies), 10)
        self.assertNotIn(
            "这副身子比例很惹眼，腰细臀翘，穿得贴一点就很难让人不多看",
            body_copies,
        )
        character = {**_sample_payload(), "id": "CUS-00888888"}
        self.assertEqual(
            _build_card_body_copy(character, 90.0),
            _build_card_body_copy(character, 90.0),
        )

    def test_card_summary_omits_beginner_and_proficient_sex_skills(self) -> None:
        character = _sample_payload()
        character["skills"] = {
            "口交": "熟练",
            "深喉": "入门",
            "职业边界管理": "宗师",
        }

        summary = build_character_card_summary(character)

        self.assertNotIn("低下头的时候", summary)
        self.assertNotIn("真正含住时", summary)

    def test_card_summary_uses_submissive_sm_skills(self) -> None:
        expected_copy = {
            "狗奴技巧": "乖乖伏在你脚边",
            "刑奴技巧": "我懂得怎样承受",
            "厕奴技巧": "把自己变成最合你心意的厕奴",
        }
        for skill, phrase in expected_copy.items():
            with self.subTest(skill=skill):
                character = _sample_payload()
                character["skills"] = {skill: "专业"}
                self.assertIn(phrase, build_character_card_summary(character))

    def test_submissive_sm_skill_wins_an_equal_level_marketing_tie(self) -> None:
        character = _sample_payload()
        character["skills"] = {"高潮控制": "专业", "狗奴技巧": "专业"}

        summary = build_character_card_summary(character)

        self.assertIn("乖乖伏在你脚边", summary)
        self.assertNotIn("没有你的允许", summary)

    def test_orgasm_control_means_the_worker_waits_for_permission(self) -> None:
        character = _sample_payload()
        character["skills"] = {"高潮控制": "精通"}

        summary = build_character_card_summary(character)

        self.assertIn("没有你的允许", summary)
        self.assertIn("等你亲口准许才敢释放", summary)
        self.assertNotIn("把你的欲望吊到最满", summary)

    def test_removed_dominant_sm_skills_are_absent_from_configuration(self) -> None:
        config_source = (PROJECT_ROOT / "world_generation/config.py").read_text(
            encoding="utf-8"
        )
        for removed in ("束缚安全", "捆绑技巧", "主导调教技巧"):
            self.assertNotIn(removed, config_source)
        for replacement in ("狗奴技巧", "刑奴技巧", "厕奴技巧"):
            self.assertIn(replacement, world_config.SKILL_CATEGORIES["adult_service"])
        self.assertEqual(
            world_config.SEX_SERVICE_REQUIRED_SKILLS["轻度调教"],
            ("刑奴技巧", "狗奴技巧"),
        )
        self.assertEqual(
            world_config.SEX_SERVICE_REQUIRED_SKILLS["重度危险调教"],
            ("高危束缚", "狗奴技巧"),
        )

    def test_creation_preview_only_renders_the_natural_summary(self) -> None:
        source = (PROJECT_ROOT / "daily_work_wallet.py").read_text(encoding="utf-8")

        self.assertIn('st.write(candidate["summary"])', source)
        self.assertIn("_character_preview_heading(candidate)", source)
        self.assertNotIn('st.subheader(candidate["name"])', source)
        self.assertNotIn('st.expander("查看完整角色信息"', source)
        self.assertNotIn("detail_labels =", source)
        self.assertIn("update_character_world_time(character)", source)
        self.assertIn("pending_daily_world_update_character_id", source)
        self.assertIn('st.chat_message("assistant", avatar="⚙️")', source)
        self.assertIn("该提示不会写入聊天记录", source)
        self.assertIn('st.session_state.page_mode = "daily_work_wallet"', source)

    def test_game_screen_keeps_active_character_portrait_on_the_left(self) -> None:
        page_source = (PROJECT_ROOT / "daily_work_wallet.py").read_text(encoding="utf-8")
        theme_source = (PROJECT_ROOT / "static/theme.css").read_text(encoding="utf-8")

        self.assertIn("_active_character_rail(active_character)", page_source)
        self.assertIn("portrait_column, game_column = st.columns", page_source)
        self.assertIn(".daily-character-rail", theme_source)
        self.assertIn("position:sticky", theme_source)

    def test_game_screen_is_chat_first_and_reports_world_update_completion(self) -> None:
        source = (PROJECT_ROOT / "daily_work_wallet.py").read_text(encoding="utf-8")

        self.assertIn("def _render_game_chat_content", source)
        self.assertIn("st.chat_input(", source)
        self.assertIn("聊天 API 与提示词尚未接入", source)
        self.assertIn("世界记录已全部生成并成功提交", source)
        self.assertIn("现在可以安全地继续游戏、关闭页面或退出程序", source)
        self.assertIn("本次数据库事务已经回滚", source)

    def test_wallet_uses_finance_snapshot_and_derives_net_assets(self) -> None:
        finances = _current_character_finances(
            {
                "id": "CUS-NOT-IN-A-WORLD",
                "cash": 2_000,
                "other_assets": 5_000,
                "debt": 9_000,
            }
        )

        self.assertEqual(finances["net_assets"], -2_000)
        self.assertEqual(_money(finances["net_assets"]), "-¥2,000")
        self.assertEqual(_money(finances["cash"]), "¥2,000")

    def test_character_sidebar_contains_expandable_wallet_and_locked_items(self) -> None:
        page_source = (PROJECT_ROOT / "daily_work_wallet.py").read_text(encoding="utf-8")

        self.assertIn('st.expander("钱包"', page_source)
        self.assertIn('st.metric("净资产"', page_source)
        self.assertIn('st.metric("现金"', page_source)
        self.assertIn('st.toggle(\n                "高利贷"', page_source)
        self.assertIn("list_player_loan_contracts", page_source)
        self.assertIn("daily_selected_loan_contract_id", page_source)
        self.assertIn("不会加入聊天记录", page_source)
        self.assertIn("📦 其他物品 · 尚未开放", page_source)
        self.assertIn("disabled=True", page_source)

    def test_character_sidebar_opens_an_automatically_scaled_city_map(self) -> None:
        page_source = (PROJECT_ROOT / "daily_work_wallet.py").read_text(encoding="utf-8")
        component_source = (
            PROJECT_ROOT / "world_generation" / "static" / "city_map" / "index.html"
        ).read_text(encoding="utf-8")

        self.assertIn('icon=":material/map:"', page_source)
        self.assertIn("load_city_map_snapshot", page_source)
        self.assertIn("load_city_map_cell_roads", page_source)
        self.assertIn("declare_component", page_source)
        self.assertIn('@st.dialog("城市地图", width="large")', page_source)
        self.assertIn("zoom<1.8?'city':zoom<4?'district':zoom<cellZoom?'street':'cell'", component_source)
        self.assertIn("city-landmark", component_source)
        self.assertIn("player-marker", component_source)
        self.assertIn("crimeMarker", component_source)
        self.assertIn('"roads": _map_roads_data', page_source)
        self.assertIn('"busStops": [', page_source)
        self.assertIn("road-district", component_source)
        self.assertIn("road-cell", component_source)
        self.assertIn("district-label", component_source)
        self.assertIn("street-label", component_source)
        self.assertIn("bus-stop", component_source)
        self.assertIn("locate-player", component_source)
        self.assertIn(".slice(0,5)", component_source)
        self.assertIn("regionOutline(district.cells)", component_source)
        self.assertNotIn("class:'cell'", component_source)
        self.assertNotIn("speed_kmh", component_source)
        self.assertNotIn("km/h", component_source)
        self.assertIn("确定要前往此处么？", component_source)
        self.assertIn("reply.mode==='bus'?'车票 ", component_source)


    def test_character_creation_dialog_cannot_be_dismissed_while_generating(self) -> None:
        source = (PROJECT_ROOT / "daily_work_wallet.py").read_text(encoding="utf-8")

        self.assertIn(
            '@st.dialog("创建新角色", width="large", dismissible=False)',
            source,
        )
        self.assertIn('if step == "generating":', source)
        self.assertIn("照片生成期间窗口已锁定", source)
        self.assertIn("_render_portrait_generation_client_timer()", source)
        self.assertIn('id="portrait-generation-elapsed"', source)
        self.assertIn("window.setInterval(renderElapsed, 1000)", source)
        self.assertIn("此计时由浏览器独立运行", source)
        self.assertIn(
            'st.session_state.daily_character_create_step = "generating"',
            source,
        )
        self.assertIn('"取消创建"', source)

    def test_saved_character_card_is_large_and_does_not_clip_summary(self) -> None:
        source = (PROJECT_ROOT / "static/theme.css").read_text(encoding="utf-8")

        self.assertIn("grid-template-columns:minmax(220px,37%)", source)
        self.assertIn(".character-card{grid-template-columns:1fr", source)
        self.assertIn("aspect-ratio:2/3", source)
        self.assertIn("max-height:560px", source)
        self.assertNotIn("-webkit-line-clamp", source)
        self.assertIn(".character-card-copy{min-width:0", source)
        self.assertIn("white-space:normal", source)

    def test_creation_preview_heading_shows_name_organization_and_title(self) -> None:
        heading = _character_preview_heading(
            {
                "name": "<玫瑰>",
                "organization_name": "夜色&会馆",
                "title": "头牌",
            }
        )

        self.assertIn("&lt;玫瑰&gt;", heading)
        self.assertIn("夜色&amp;会馆", heading)
        self.assertIn("头牌", heading)
        self.assertIn("font-size:2.35rem", heading)
        self.assertIn("font-weight:850", heading)
        self.assertNotIn("<玫瑰>", heading)

    def test_excluded_reference_character_is_not_reused(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "city.sqlite3"
            _build_fixture_database(database)
            with self.assertRaisesRegex(LookupError, "没有找到"):
                draw_world_character(
                    "妓女",
                    database=database,
                    excluded_source_ids={"CUS-00000042"},
                )

    def test_world_discovery_uses_valid_database_files_not_a_fixed_name(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = root / "alpha_world.sqlite3"
            second = root / "beta_country.sqlite3"
            invalid = root / "notes.sqlite3"
            _build_fixture_database(first)
            _build_fixture_database(second)
            sqlite3.connect(invalid).close()

            worlds = discover_character_worlds(root)

        self.assertEqual(
            [world.world_id for world in worlds],
            ["alpha_world.sqlite3", "beta_country.sqlite3"],
        )
        self.assertEqual(worlds[0].display_name, "alpha_world · 测试世界")

    def test_exclusions_are_scoped_to_the_selected_world(self) -> None:
        existing = [
            {
                "source_world_id": "alpha.sqlite3",
                "source_character_id": "CUS-00000042",
            },
            {
                "source_world_id": "beta.sqlite3",
                "source_character_id": "CUS-00000077",
            },
        ]
        self.assertEqual(
            excluded_source_ids_for_world(existing, "alpha.sqlite3"),
            {"CUS-00000042"},
        )

    def test_prompt_uses_character_description_service_output(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "city.sqlite3"
            _build_fixture_database(database)
            candidate = draw_world_character("妓女", database=database)

        prompt = build_character_portrait_prompt(candidate)
        descriptions = build_portrait_descriptions(candidate)
        self.assertIn(descriptions["face"], prompt)
        self.assertIn(descriptions["body_metrics"], prompt)
        self.assertIn(descriptions["body_shape"], prompt)
        self.assertFalse(any("\u3400" <= char <= "\u9fff" for char in prompt))
        self.assertNotIn("The selected outfit is fixed", prompt)

    def test_portrait_prompt_filters_negative_personality_and_uses_venue_scene(self) -> None:
        character = _sample_payload()
        character.update(
            {
                "occupation": "发廊妹",
                "organization_template": "adult_hair_salon",
                "personality_tags": ["虚荣拜金", "恶毒腹黑", "温和"],
                "attractiveness_grade": "长相漂亮",
                "body_grade": "身材出众",
            }
        )

        with patch("world_generation.services.character_description.random.choice", side_effect=lambda values: values[0]):
            prompt = build_character_portrait_prompt(character)

        self.assertIn("cramped, shabby partitioned back room", prompt)
        self.assertIn("low-budget, worn and functional", prompt)
        self.assertIn("dye-stained towels", prompt)
        self.assertNotIn("upscale hotel room", prompt)
        self.assertNotIn("虚荣拜金", prompt)
        self.assertNotIn("恶毒腹黑", prompt)
        self.assertNotIn("温和", prompt)
        self.assertIn("soft, gentle and warmly inviting expression", prompt)
        self.assertIn("cheap-looking pink satin camisole", prompt)
        self.assertNotIn("exceptionally attractive, refined and highly sexually appealing", prompt)
        self.assertNotIn("Every visual element must maximize", prompt)
        self.assertNotIn("strikingly beautiful", prompt)
        self.assertNotIn("do not replace it", prompt)

    def test_portrait_prompt_omits_personality_when_all_tags_are_negative(self) -> None:
        character = _sample_payload()
        character.update(
            {
                "occupation": "冰妹",
                "organization_template": "adult_nightclub",
                "personality_tags": ["嫉妒成性", "欺软怕硬", "性饥渴"],
                "attractiveness_grade": "长相漂亮",
                "body_grade": "身材出众",
            }
        )

        prompt = build_character_portrait_prompt(character)

        self.assertNotIn("personality traits", prompt)
        self.assertNotIn("嫉妒成性", prompt)
        self.assertIn("nightclub VIP lounge", prompt)
        self.assertIn("slightly glazed and unfocused", prompt)

    def test_every_sex_service_venue_template_has_a_distinct_portrait_scene(self) -> None:
        cases = {
            "street_prostitution_ring": ("站街女", "urban side street at night"),
            "adult_hair_salon": ("发廊妹", "adult hair salon"),
            "adult_massage_parlor": ("妓女", "modest massage parlor"),
            "adult_leisure_house": ("性奴", "neighborhood leisure house"),
            "ordinary_brothel": ("妓女", "ordinary brothel"),
            "luxury_business_ktv": ("妓女", "business-KTV room"),
            "adult_nightclub": ("冰妹", "nightclub VIP lounge"),
            "luxury_brothel": ("SM妓女", "luxury brothel"),
            "adult_club": ("妓女", "members-only club suite"),
        }
        for template, (occupation, expected_scene) in cases.items():
            with self.subTest(template=template):
                character = _sample_payload()
                character.update(
                    {
                        "occupation": occupation,
                        "organization_template": template,
                        "attractiveness_grade": "长相漂亮",
                        "body_grade": "身材出众",
                    }
                )
                prompt = build_character_portrait_prompt(character)
                self.assertIn(expected_scene, prompt)
                self.assertNotIn("upscale hotel room", prompt)

    def test_sex_worker_level_does_not_change_portrait_prompt(self) -> None:
        character = _sample_payload()
        character.update(
            {
                "organization_template": "ordinary_brothel",
                "attractiveness_grade": "长相漂亮",
                "body_grade": "身材出众",
            }
        )
        with patch("world_generation.services.character_description.random.choice", side_effect=lambda values: values[0]):
            character["sex_worker_level"] = 1
            low_level_prompt = build_character_portrait_prompt(character)
            character["sex_worker_level"] = 9
            high_level_prompt = build_character_portrait_prompt(character)

        self.assertEqual(low_level_prompt, high_level_prompt)

    def test_slave_occupation_label_is_absent_from_portrait_prompt(self) -> None:
        character = _sample_payload()
        character["occupation"] = "性奴"
        character["title"] = "性奴"

        prompt = build_character_portrait_prompt(character)

        self.assertNotIn("性奴", prompt)
        self.assertNotIn("sex slave", prompt.casefold())
        self.assertNotIn("sexual slave", prompt.casefold())

    def test_portrait_prompts_contain_no_character_action_requirements(self) -> None:
        forbidden_actions = re.compile(
            r"\b(?:kneel(?:ing)?|reclin(?:e|ing)|stand(?:ing|s)?|stance|"
            r"seated|sitting|pose|posing|posture|body language|"
            r"crouch(?:ing)?|squat(?:ting)?|crawl(?:ing)?)\b",
            flags=re.IGNORECASE,
        )
        for occupation in ("站街女", "妓女", "冰妹", "性奴", "SM妓女", "发廊妹"):
            for body_grade in ("身材较差", "身材普通", "身材出众", "身材极佳"):
                for variant in range(24):
                    character = _sample_payload()
                    character.update({
                        "id": f"CUS-ACTION-{occupation}-{body_grade}-{variant}",
                        "occupation": occupation,
                        "title": occupation,
                        "body_grade": body_grade,
                    })
                    prompt = build_character_portrait_prompt(character)
                    self.assertIsNone(
                        forbidden_actions.search(prompt),
                        msg=f"{occupation}/{body_grade}: {prompt}",
                    )

    def test_prompt_builder_has_a_single_service_implementation(self) -> None:
        customer_source = (PROJECT_ROOT / "customer_characters.py").read_text(
            encoding="utf-8"
        )
        self.assertIs(build_character_portrait_prompt, service_portrait_prompt)
        self.assertNotIn("CLOTHING_POOL_BY_TYPE", customer_source)
        self.assertNotIn("This is a professional promotional photograph", customer_source)
        catalog_source = (
            PROJECT_ROOT / "world_generation/services/character_catalog.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("现居", catalog_source)
        self.assertNotIn("身份为", catalog_source)

    def test_confirmation_preserves_the_world_character_id(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            database = root / "city.sqlite3"
            _build_fixture_database(database)
            candidate = draw_world_character("妓女", database=database)
            encoded = base64.b64encode(b"\x89PNG\r\n\x1a\nmock").decode("ascii")
            client = SimpleNamespace(
                images=SimpleNamespace(
                    generate=lambda **_: SimpleNamespace(
                        data=[SimpleNamespace(b64_json=encoded)]
                    )
                )
            )
            portraits = root / "portraits"
            portraits.mkdir()
            preserved_asset = portraits / "CUS-00000042.png"
            preserved_asset.write_bytes(b"preserved portrait asset")
            with patch("customer_characters.OpenAI", return_value=client):
                confirmed = confirm_city_character(
                    candidate,
                    api_key="test-key",
                    portraits_dir=portraits,
                    storage_root=root,
                )

            self.assertEqual(confirmed["id"], "CUS-00000042")
            self.assertEqual(
                confirmed["player_organization"],
                confirmed["organization_id"],
            )
            self.assertEqual(
                confirmed["source_organization_id"],
                confirmed["organization_id"],
            )
            self.assertEqual(preserved_asset.read_bytes(), b"preserved portrait asset")
            self.assertNotEqual(root / confirmed["photo_path"], preserved_asset)
            self.assertTrue((root / confirmed["photo_path"]).is_file())

    def test_portrait_generation_reports_attempt_failure_retry_and_completion(self) -> None:
        candidate = _sample_payload()
        candidate.update({
            "source_character_id": "CUS-00000042",
            "organization_id": "ORG-00000007",
        })
        encoded = base64.b64encode(b"\x89PNG\r\n\x1a\nmock").decode("ascii")
        generate = Mock(side_effect=[
            RuntimeError("content moderation rejected the first request"),
            SimpleNamespace(data=[SimpleNamespace(b64_json=encoded)]),
        ])
        client = SimpleNamespace(images=SimpleNamespace(generate=generate))
        events: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with (
                patch("customer_characters.OpenAI", return_value=client),
                patch("customer_characters.time.sleep"),
            ):
                confirm_city_character(
                    candidate,
                    api_key="test-key",
                    portraits_dir=root / "portraits",
                    storage_root=root,
                    progress_callback=events.append,
                )

        self.assertEqual(generate.call_count, 2)
        self.assertEqual(
            [event["stage"] for event in events],
            ["attempt_started", "attempt_failed", "attempt_started", "image_received", "completed"],
        )
        self.assertEqual(events[1]["attempt"], 1)
        self.assertTrue(events[1]["will_retry"])
        self.assertEqual(events[-1]["attempt"], 2)

    def test_delete_character_removes_record_but_retains_portrait_asset(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            portraits = root / "portraits"
            portraits.mkdir()
            portrait = portraits / "CUS-1.png"
            portrait.write_bytes(b"portrait")
            characters_path = root / "characters.json"
            characters_path.write_text(
                json.dumps([
                    {
                        "id": "CUS-1",
                        "name": "待删除",
                        "summary": "简介",
                        "photo_path": "portraits/CUS-1.png",
                        "source_world_id": "world.sqlite3",
                        "source_character_id": "CUS-1",
                        "source_organization_id": "ORG-1",
                        "player_organization": "ORG-1",
                    },
                    {
                        "id": "CUS-2",
                        "name": "保留",
                        "summary": "简介",
                        "photo_path": "portraits/CUS-2.png",
                        "source_world_id": "world.sqlite3",
                        "source_character_id": "CUS-2",
                        "source_organization_id": "ORG-2",
                        "player_organization": "ORG-2",
                    },
                ], ensure_ascii=False),
                encoding="utf-8",
            )

            removed = delete_customer_character(
                "CUS-1",
                path=characters_path,
            )

            self.assertEqual(removed["id"], "CUS-1")
            self.assertTrue(portrait.exists())
            self.assertEqual(portrait.read_bytes(), b"portrait")
            self.assertEqual(
                [character["id"] for character in load_customer_characters(characters_path)],
                ["CUS-2"],
            )

    def test_delete_world_is_blocked_by_players_then_removes_related_files(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            database = root / "deletable.sqlite3"
            _build_fixture_database(database)
            report = root / "deletable_statistics.json"
            report.write_text("{}", encoding="utf-8")
            characters_path = root / "characters.json"
            characters_path.write_text(
                json.dumps([{
                    "id": "CUS-1",
                    "name": "引用者",
                    "summary": "简介",
                    "photo_path": "portraits/CUS-1.png",
                    "source_world_id": database.name,
                    "source_character_id": "CUS-1",
                    "source_organization_id": "ORG-00000007",
                    "player_organization": "ORG-00000007",
                }], ensure_ascii=False),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "仍被 1 个已创建角色使用"):
                delete_character_world(
                    database.name,
                    data_directory=root,
                    characters_path=characters_path,
                )
            self.assertTrue(database.exists())

            characters_path.write_text("[]", encoding="utf-8")
            removed = delete_character_world(
                database.name,
                data_directory=root,
                characters_path=characters_path,
            )

            self.assertIn(database.resolve(), removed)
            self.assertFalse(database.exists())
            self.assertFalse(report.exists())

    def test_create_saves_once_and_rejects_duplicate_before_api_call(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            database = root / "city.sqlite3"
            _build_fixture_database(database)
            candidate = draw_world_character("妓女", database=database)
            encoded = base64.b64encode(b"\x89PNG\r\n\x1a\nmock").decode("ascii")
            client = SimpleNamespace(
                images=SimpleNamespace(
                    generate=lambda **_: SimpleNamespace(
                        data=[SimpleNamespace(b64_json=encoded)]
                    )
                )
            )
            characters_path = root / "characters.json"
            with (
                patch("customer_characters.OpenAI", return_value=client) as openai,
                patch(
                    "customer_characters.update_character_world_time",
                    return_value=True,
                ) as update_world,
                patch(
                    "customer_characters.materialize_character_debt",
                    return_value=(),
                ) as materialize_debt,
                patch("customer_characters.initialize_character_location") as initialize_location,
            ):
                created = create_and_save_customer_character(
                    candidate,
                    api_key="test-key",
                    characters_path=characters_path,
                    portraits_dir=root / "portraits",
                    storage_root=root,
                )
                with self.assertRaisesRegex(ValueError, "不能重复添加"):
                    create_and_save_customer_character(
                        candidate,
                        api_key="test-key",
                        characters_path=characters_path,
                        portraits_dir=root / "portraits",
                        storage_root=root,
                    )

            saved = json.loads(characters_path.read_text(encoding="utf-8"))
            self.assertEqual(created["id"], "CUS-00000042")
            self.assertEqual(saved[0]["id"], "CUS-00000042")
            self.assertEqual(openai.call_count, 1)
            update_world.assert_called_once_with(created)
            materialize_debt.assert_called_once_with(created)
            initialize_location.assert_called_once_with(created)

    def test_character_creation_can_defer_world_update_until_game_screen(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            database = root / "city.sqlite3"
            _build_fixture_database(database)
            candidate = draw_world_character("妓女", database=database)
            encoded = base64.b64encode(b"\x89PNG\r\n\x1a\nmock").decode("ascii")
            client = SimpleNamespace(
                images=SimpleNamespace(
                    generate=lambda **_: SimpleNamespace(
                        data=[SimpleNamespace(b64_json=encoded)]
                    )
                )
            )
            with (
                patch("customer_characters.OpenAI", return_value=client),
                patch("customer_characters.update_character_world_time") as update_world,
                patch(
                    "customer_characters.materialize_character_debt",
                    return_value=(),
                ) as materialize_debt,
                patch("customer_characters.initialize_character_location") as initialize_location,
            ):
                created = create_and_save_customer_character(
                    candidate,
                    api_key="test-key",
                    characters_path=root / "characters.json",
                    portraits_dir=root / "portraits",
                    storage_root=root,
                    initialize_world_time=False,
                )

            self.assertEqual(created["id"], "CUS-00000042")
            update_world.assert_not_called()
            materialize_debt.assert_called_once_with(created)

    def test_world_time_update_forwards_world_and_player_organization(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "city.sqlite3"
            database.touch()
            character = {
                "source_world_id": "city.sqlite3",
                "player_organization": "ORG-PLAYER",
            }
            now = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
            with patch(
                "world_generation.services.time_update.update_time",
                return_value=True,
            ) as update:
                self.assertTrue(update_character_world_time(
                    character,
                    database=database,
                    now=now,
                ))
            update.assert_called_once_with(
                database.resolve(),
                now=now,
                player_organization_id="ORG-PLAYER",
            )

    def test_player_organization_can_change_without_losing_origin(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "characters.json"
            path.write_text(
                json.dumps(
                    [{
                        "id": "CUS-00000042",
                        "name": "测试角色",
                        "summary": "测试简介",
                        "photo_path": "portraits/test.png",
                        "source_world_id": "world.sqlite3",
                        "source_character_id": "CUS-00000042",
                        "source_organization_id": "ORG-OLD",
                        "player_organization": "ORG-OLD",
                        "organization_id": "ORG-OLD",
                        "organization_name": "旧组织",
                        "title": "旧职位",
                        "profile": {
                            "所属组织": "旧组织",
                            "组织身份": "旧职位",
                        },
                    }],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            updated = update_player_organization(
                "CUS-00000042",
                "ORG-NEW",
                organization_name="新组织",
                title="新职位",
                path=path,
            )

            saved = json.loads(path.read_text(encoding="utf-8"))[0]
            self.assertEqual(updated["player_organization"], "ORG-NEW")
            self.assertEqual(saved["organization_id"], "ORG-NEW")
            self.assertEqual(saved["source_organization_id"], "ORG-OLD")
            self.assertEqual(saved["organization_name"], "新组织")
            self.assertEqual(saved["profile"]["所属组织"], "新组织")
            self.assertEqual(saved["profile"]["组织身份"], "新职位")

    def test_change_player_organization_invalidates_affected_future_visits(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            database = root / "city.sqlite3"
            _build_fixture_database(database)
            characters_path = root / "characters.json"
            characters_path.write_text(
                json.dumps(
                    [{
                        "id": "CUS-00000042",
                        "source_character_id": "CUS-00000042",
                        "source_world_id": database.name,
                        "name": "测试角色",
                        "summary": "测试简介",
                        "photo_path": "portraits/test.png",
                        "organization_id": "ORG-00000007",
                        "organization_name": "测试会馆",
                        "source_organization_id": "ORG-00000007",
                        "player_organization": "ORG-00000007",
                        "profile": {"所属组织": "测试会馆"},
                    }],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            effective = datetime(2026, 10, 10, 8, 0, tzinfo=timezone.utc)
            future = (effective + timedelta(days=1)).isoformat(timespec="seconds")
            past = (effective - timedelta(days=1)).isoformat(timespec="seconds")
            with closing(sqlite3.connect(database)) as connection:
                connection.executescript(
                    """
                    CREATE TABLE sex_service_visit_schedule (
                        visit_id INTEGER PRIMARY KEY,
                        scheduled_at TEXT NOT NULL,
                        status TEXT NOT NULL,
                        free_access INTEGER NOT NULL,
                        organization_id TEXT,
                        worker_character_id TEXT,
                        service_name TEXT,
                        quoted_price INTEGER,
                        customer_charge INTEGER,
                        processed_at TEXT
                    );
                    """
                )
                connection.executemany(
                    """
                    INSERT INTO sex_service_visit_schedule VALUES (
                        ?, ?, 'planned', 1, ?, 'CUS-00000042',
                        '普通性交', 1000, 0, NULL
                    )
                    """,
                    (
                        (1, future, "ORG-00000007"),
                        (2, future, "ORG-NEW"),
                        (3, future, "ORG-OTHER"),
                        (4, past, "ORG-00000007"),
                    ),
                )
                connection.commit()

            updated = change_player_organization(
                "CUS-00000042",
                "ORG-NEW",
                title="新职位",
                effective_at=effective,
                database=database,
                path=characters_path,
            )

            self.assertEqual(updated["player_organization"], "ORG-NEW")
            self.assertEqual(updated["organization_name"], "新组织")
            self.assertEqual(updated["invalidated_future_sex_service_visits"], 2)
            with closing(sqlite3.connect(database)) as connection:
                visits = connection.execute(
                    """
                    SELECT visit_id, organization_id, worker_character_id,
                           service_name, quoted_price, customer_charge, free_access
                    FROM sex_service_visit_schedule
                    ORDER BY visit_id
                    """
                ).fetchall()
            self.assertEqual(visits[0], (1, None, None, None, None, None, 0))
            self.assertEqual(visits[1], (2, None, None, None, None, None, 0))
            self.assertEqual(visits[2][1], "ORG-OTHER")
            self.assertEqual(visits[3][1], "ORG-00000007")

class StreamlitFrontendSmokeTests(unittest.TestCase):
    def test_character_selection_page_and_dialog_render(self) -> None:
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_file(str(PROJECT_ROOT / "app.py"), default_timeout=20).run()
        self.assertFalse(app.exception)
        next(button for button in app.button if button.label == "日常工作与钱包").click()
        app.run()
        self.assertFalse(app.exception)
        next(button for button in app.button if button.label == "✦ 创建新角色").click()
        app.run()
        self.assertFalse(app.exception)
        world_missing = any(
            "没有可用的世界数据库" in error.value for error in app.error
        )
        if world_missing:
            self.assertFalse(
                any(button.label == "抽选角色" for button in app.button)
            )
            self.assertFalse(
                any(selectbox.label == "世界" for selectbox in app.selectbox)
            )
        else:
            self.assertTrue(
                any(button.label == "抽选角色" for button in app.button)
            )
            self.assertTrue(
                any(selectbox.label == "世界" for selectbox in app.selectbox)
            )


if __name__ == "__main__":
    unittest.main()
