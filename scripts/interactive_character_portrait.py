"""Interactively review a generated character prompt before requesting a portrait.

The script uses the same world-character adapter and portrait prompt builder as
the frontend.  It does not create a player character, reserve an ID, update
world time, or write to the selected world database.

Examples::

    python -m world_generation.scripts.interactive_character_portrait
    python -m world_generation.scripts.interactive_character_portrait --occupation SM妓女
    python -m world_generation.scripts.interactive_character_portrait --world test_1.sqlite3
"""

from __future__ import annotations

import argparse
import base64
import os
import random
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Sequence

from dotenv import load_dotenv
from openai import APIConnectionError, APIStatusError, OpenAI


WORLD_GENERATION_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = WORLD_GENERATION_ROOT.parent
DATA_DIR = WORLD_GENERATION_ROOT / "data"
DEFAULT_OUTPUT_DIR = DATA_DIR / "test_character_portraits"
DEFAULT_MODEL = "grok-imagine-image-2.0"
OCCUPATIONS = ("站街女", "妓女", "冰妹", "性奴", "SM妓女", "发廊妹")

# Allow direct execution as well as ``python -m world_generation.scripts...``.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from world_generation.services.character_catalog import (  # noqa: E402
    CharacterWorldDatabase,
    discover_character_worlds,
    draw_reference_character,
)
from world_generation.services.character_description import (  # noqa: E402
    build_character_portrait_prompt,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="随机抽取性工作者、审核完整 prompt，确认后调用 xAI 生成一张照片。",
    )
    parser.add_argument(
        "--world",
        help=(
            "世界数据库的文件名、世界名或路径；默认优先使用 "
            "world_generation/data/test_1.sqlite3。"
        ),
    )
    parser.add_argument(
        "--occupation",
        choices=(*OCCUPATIONS, "随机"),
        default="随机",
        help="限定职业；默认从全部六种性工作者中随机抽取。",
    )
    parser.add_argument(
        "--seed",
        type=int,
        help="可选的抽样随机种子；同一世界和种子便于复查抽样顺序。",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("XAI_IMAGE_MODEL", DEFAULT_MODEL),
        help=f"图片模型，默认 {DEFAULT_MODEL}。",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="照片保存目录。",
    )
    return parser


def _world_candidates() -> list[CharacterWorldDatabase]:
    worlds = discover_character_worlds(DATA_DIR)
    if not worlds:
        raise FileNotFoundError(f"{DATA_DIR} 中没有可用的完整世界数据库")
    return worlds


def resolve_world_database(selector: str | None) -> CharacterWorldDatabase:
    worlds = _world_candidates()
    if selector:
        supplied_path = Path(selector).expanduser()
        path_candidates = (
            supplied_path,
            Path.cwd() / supplied_path,
            DATA_DIR / supplied_path,
        )
        resolved_paths = {
            candidate.resolve()
            for candidate in path_candidates
            if candidate.is_file()
        }
        for world in worlds:
            if world.database in resolved_paths:
                return world
            if selector in {world.world_id, world.name, world.database.stem}:
                return world
        available = "、".join(world.world_id for world in worlds)
        raise FileNotFoundError(
            f"找不到指定世界“{selector}”；当前可用世界：{available}"
        )

    preferred = next(
        (world for world in worlds if world.world_id == "test_1.sqlite3"),
        None,
    )
    return preferred or worlds[0]


def _print_candidate(
    character: dict,
    prompt: str,
    world: CharacterWorldDatabase,
) -> None:
    print("\n" + "=" * 88)
    print("抽中的测试角色")
    print("=" * 88)
    print(f"世界：{world.name}（{world.database.name}）")
    print(f"角色ID：{character['id']}")
    print(f"姓名：{character['name']}")
    print(f"年龄：{character['age']} 岁")
    print(f"职业：{character['occupation']}")
    print(f"组织：{character['organization_name']}")
    print(f"组织模板：{character.get('organization_template') or '旧数据库未提供'}")
    print(
        f"综合外貌：{character['attractiveness_grade']} "
        f"({character['appearance_score']:.2f})"
    )
    print(f"面部评分：{character['face_score']:.2f}")
    print(f"身材评价：{character['body_grade']} ({character['body_score']:.2f})")
    print(f"性工作者等级：{character.get('sex_worker_level', '无')}")
    print("\n发送给图片模型的完整 prompt")
    print("-" * 88)
    print(prompt)
    print("-" * 88)


def _review_choice() -> str:
    while True:
        try:
            choice = input(
                "请选择：[g] 确认并生成照片  [r] 重新抽取角色和 prompt  [q] 退出："
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消，未调用图片接口。")
            return "quit"
        if choice in {"g", "generate", "y", "yes", "生成", "确认"}:
            return "generate"
        if choice in {"r", "reroll", "n", "no", "重选", "重新抽取"}:
            return "reroll"
        if choice in {"q", "quit", "exit", "退出"}:
            return "quit"
        print("无法识别该输入，请输入 g、r 或 q。")


def review_until_confirmed(
    world: CharacterWorldDatabase,
    occupation: str,
    *,
    rng: random.Random | None = None,
) -> tuple[dict, str] | None:
    excluded_ids: set[str] = set()
    while True:
        try:
            character = draw_reference_character(
                occupation,
                database=world.database,
                excluded_source_ids=excluded_ids,
                rng=rng,
            )
        except LookupError:
            if not excluded_ids:
                raise
            print("\n本轮符合条件的角色已经全部看过，将清空排除列表后继续抽取。")
            excluded_ids.clear()
            continue
        excluded_ids.add(str(character["source_character_id"]))
        prompt = build_character_portrait_prompt(character)
        _print_candidate(character, prompt, world)
        choice = _review_choice()
        if choice == "generate":
            return character, prompt
        if choice == "reroll":
            print("\n正在重新抽取角色并生成新的 prompt……")
            continue
        if choice == "quit":
            return None


def _image_bytes(image: object) -> tuple[bytes, str]:
    encoded = getattr(image, "b64_json", None)
    if encoded:
        payload = base64.b64decode(encoded, validate=True)
    else:
        url = getattr(image, "url", None)
        if not url:
            raise RuntimeError("图片响应中既没有 b64_json，也没有 URL")
        with urllib.request.urlopen(url, timeout=60) as response:
            payload = response.read()

    if payload.startswith(b"\xff\xd8\xff"):
        return payload, ".jpg"
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return payload, ".png"
    if payload.startswith(b"RIFF") and payload[8:12] == b"WEBP":
        return payload, ".webp"
    raise RuntimeError("图片接口返回了无法识别的文件格式")


def generate_portrait(
    character: dict,
    prompt: str,
    *,
    model: str,
    output_dir: Path,
) -> Path:
    # Deliberately load and validate credentials only after explicit approval.
    load_dotenv(PROJECT_ROOT / ".env")
    load_dotenv(WORLD_GENERATION_ROOT / ".env")
    api_key = os.getenv("XAI_API_KEY", "").strip()
    if not api_key or api_key == "xai-your-api-key":
        raise RuntimeError("没有有效的 XAI_API_KEY，请先在项目 .env 中配置")

    print(f"\n正在调用 {model} 生成一张 2:3、medium、1K 照片……")
    response = OpenAI(
        api_key=api_key,
        base_url=os.getenv("XAI_BASE_URL", "https://api.x.ai/v1"),
        timeout=90.0,
    ).images.generate(
        model=model,
        prompt=prompt,
        response_format="b64_json",
        extra_body={
            "aspect_ratio": "2:3",
            "quality": "medium",
            "resolution": "1k",
        },
    )
    if not getattr(response, "data", None):
        raise RuntimeError("图片接口返回成功响应，但其中没有图片数据")
    payload, extension = _image_bytes(response.data[0])

    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    source_id = "".join(
        char
        for char in str(character["id"])
        if char.isalnum() or char in "-_"
    )
    destination = output_dir / f"{timestamp}_{source_id}{extension}"
    destination.write_bytes(payload)
    return destination


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        world = resolve_world_database(args.world)
        rng = random.Random(args.seed) if args.seed is not None else None
        reviewed = review_until_confirmed(world, args.occupation, rng=rng)
        if reviewed is None:
            return 0
        character, prompt = reviewed
        destination = generate_portrait(
            character,
            prompt,
            model=args.model,
            output_dir=args.output_dir,
        )
    except APIConnectionError as error:
        print(f"失败：无法连接图片接口，请检查代理、VPN 或 DNS。\n{error}")
        return 3
    except APIStatusError as error:
        print(f"失败：图片接口返回 HTTP {error.status_code}。\n{error.message}")
        return 4
    except (FileNotFoundError, LookupError, RuntimeError, ValueError) as error:
        print(f"失败：{error}")
        return 2
    except Exception as error:  # pragma: no cover - final diagnostic boundary
        print(f"失败：{type(error).__name__}: {error}")
        return 5

    print(f"\n生成成功，照片已保存到：\n{destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
