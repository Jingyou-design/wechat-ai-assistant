"""生成有尺寸和体积上限的模型图片输入，不保留 EXIF 等原始元数据。"""

import io
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError


def preview(path: Path) -> tuple[bytes, int, int]:
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("图片文件过大，无法生成预览。")
    try:
        with Image.open(path) as source:
            if source.width * source.height > 20_000_000:
                raise ValueError("图片像素过大，无法生成预览。")
            image = ImageOps.exif_transpose(source).convert("RGBA")
            image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
            background = Image.new("RGB", image.size, "white")
            background.paste(image, mask=image.getchannel("A"))
            output = io.BytesIO()
            background.save(output, format="JPEG", quality=85, optimize=True)
            data = output.getvalue()
            while len(data) > 512 * 1024:
                background.thumbnail(
                    (
                        max(1, background.width * 3 // 4),
                        max(1, background.height * 3 // 4),
                    ),
                    Image.Resampling.LANCZOS,
                )
                output = io.BytesIO()
                background.save(output, format="JPEG", quality=85, optimize=True)
                data = output.getvalue()
            return data, background.width, background.height
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
        raise ValueError("图片缓存格式无法解码，暂时不能查看。") from None
