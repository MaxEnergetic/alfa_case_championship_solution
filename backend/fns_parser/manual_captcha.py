"""Human-only image challenges. Never execute or proxy an upstream HTML page."""

import base64
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import parse_qs, urlsplit

from PIL import Image, UnidentifiedImageError

from .html_tools import Tree
from .transport import SourceError


@dataclass(frozen=True)
class CaptchaImage:
    content: bytes
    media_type: str


def fssp_challenge(html: str, inn: str) -> CaptchaImage | None:
    tree = Tree(html).root
    form = next(tree.find("form", id="ncapcha"), None)
    if form is None:
        return None
    # Bind the displayed challenge to the pending public query, never to a URL
    # supplied by the user. The upstream form is inspected, not embedded.
    url = urlsplit(form.attrs.get("url") or "")
    query = parse_qs(url.query)
    if (url.scheme or url.netloc or url.path != "/ajax_search"
            or query.get("system") != ["ip"] or query.get("is[variant]") != ["5"]
            or query.get("is[inn]") != [inn] or form.attrs.get("method", "").lower() != "get"):
        raise SourceError("invalid_response", "Капча ФССП не соответствует исходному запросу")
    code = next((i for i in form.find("input") if i.attrs.get("name") == "code"), None)
    img = next(form.find("img", id="capchaVisual"), None)
    if code is None or img is None:
        raise SourceError("invalid_response", "Не распознана форма ручного ввода ФССП")
    prefix, sep, encoded = (img.attrs.get("src") or "").partition(",")
    if not sep or prefix not in ("data:image/jpeg;base64", "data:image/png;base64") or len(encoded) > 1400000:
        raise SourceError("invalid_response", "Неизвестный формат изображения капчи")
    try:
        content = base64.b64decode(encoded, validate=True)
        with Image.open(BytesIO(content)) as image:
            expected = "JPEG" if "jpeg" in prefix else "PNG"
            if image.format != expected or not (1 <= image.width <= 2048 and 1 <= image.height <= 2048):
                raise ValueError("Invalid image")
            image.verify()
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise SourceError("invalid_response", "Повреждённое изображение капчи") from exc
    return CaptchaImage(content, prefix[5:].split(";")[0])
