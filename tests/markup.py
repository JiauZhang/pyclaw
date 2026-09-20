import re


def plain(text: str) -> str:
    return re.sub(r"\[?/?[^\]\[\n]*\]", "", text).replace("\\[", "[")
