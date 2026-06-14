"""PWA用アセット生成スクリプト（一回限りの実行用）。

static/icons/icon-512.png（SKY DINING UOMANロゴ）を元に、
- Android用maskableアイコン（セーフゾーン80%でパディング）
- iOS用スプラッシュスクリーン（apple-touch-startup-image）
- スプラッシュスクリーンの<link>タグを含むテンプレート

を生成する。ロゴ画像を変更した場合に再実行する。

実行方法:
    python scripts/seed_test_data.py のように、リポジトリルートから:
    python scripts/generate_pwa_assets.py
"""
from __future__ import annotations

from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ICONS_DIR = ROOT / "static" / "icons"
SPLASH_DIR = ICONS_DIR / "splash"
TEMPLATE_OUT = ROOT / "templates" / "pwa" / "_splash_links.html"

BG_COLOR = (255, 255, 255, 255)  # manifest.json の background_color と一致

# iOSスプラッシュスクリーン対象デバイス
# (出力ファイル幅, 出力ファイル高さ, device-width, device-height, dpr, orientation)
SPLASH_DEVICES = [
    (750,  1334, 375, 667,  2, "portrait",  "iPhone SE/7/8"),
    (1125, 2436, 375, 812,  3, "portrait",  "iPhone X/11 Pro/12-13 mini"),
    (1170, 2532, 390, 844,  3, "portrait",  "iPhone 12/13/14"),
    (1179, 2556, 393, 852,  3, "portrait",  "iPhone 14 Pro/15/16"),
    (1290, 2796, 430, 932,  3, "portrait",  "iPhone 14-16 Plus/Pro Max"),
    (1668, 2388, 834, 1194, 2, "portrait",  "iPad Air/Pro 11インチ（縦）"),
    (2388, 1668, 834, 1194, 2, "landscape", "iPad Air/Pro 11インチ（横）"),
]


def make_maskable(src: Image.Image, size: int, out: Path) -> None:
    """セーフゾーン80%でロゴを中央配置したmaskableアイコンを生成"""
    canvas = Image.new("RGBA", (size, size), BG_COLOR)
    inner = round(size * 0.8)
    logo = src.resize((inner, inner), Image.LANCZOS)
    offset = (size - inner) // 2
    canvas.paste(logo, (offset, offset), logo)
    canvas.save(out)
    print(f"generated {out.relative_to(ROOT)}")


def make_splash(src: Image.Image, width: int, height: int, out: Path) -> None:
    """背景色のキャンバス中央にロゴを配置したスプラッシュ画像を生成"""
    canvas = Image.new("RGBA", (width, height), BG_COLOR)
    logo_size = round(min(width, height) * 0.35)
    logo = src.resize((logo_size, logo_size), Image.LANCZOS)
    pos = ((width - logo_size) // 2, (height - logo_size) // 2)
    canvas.paste(logo, pos, logo)
    canvas.convert("RGB").save(out)
    print(f"generated {out.relative_to(ROOT)}")


def main() -> None:
    src = Image.open(ICONS_DIR / "icon-512.png").convert("RGBA")

    make_maskable(src, 512, ICONS_DIR / "icon-512-maskable.png")
    make_maskable(src, 192, ICONS_DIR / "icon-192-maskable.png")

    SPLASH_DIR.mkdir(parents=True, exist_ok=True)
    links = []
    for width, height, dw, dh, dpr, orientation, label in SPLASH_DEVICES:
        filename = f"apple-splash-{width}-{height}.png"
        make_splash(src, width, height, SPLASH_DIR / filename)
        media = (
            f"(device-width: {dw}px) and (device-height: {dh}px) "
            f"and (-webkit-device-pixel-ratio: {dpr}) and (orientation: {orientation})"
        )
        links.append(
            f'  <!-- {label} -->\n'
            f'  <link rel="apple-touch-startup-image" media="{media}" '
            f'href="/static/icons/splash/{filename}">'
        )

    TEMPLATE_OUT.parent.mkdir(parents=True, exist_ok=True)
    TEMPLATE_OUT.write_text(
        "<!-- iOSスプラッシュスクリーン（scripts/generate_pwa_assets.py で自動生成） -->\n"
        + "\n".join(links) + "\n"
    )
    print(f"generated {TEMPLATE_OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
