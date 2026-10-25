"""Keep identifiers as strings, validate length and check digits before I/O."""

import re


def validate_identifier(value: str) -> tuple[str, str]:
    if not isinstance(value, str):
        raise ValueError("ИНН/ОГРН необходимо передать строкой")
    value = re.sub(r"\s+", "", value)
    if not re.fullmatch(r"[0-9]{10}|[0-9]{12}|[0-9]{13}|[0-9]{15}", value):
        raise ValueError("Нужны 10/12 цифр ИНН, 13 цифр ОГРН или 15 цифр ОГРНИП")
    if set(value) == {"0"}:
        raise ValueError("Идентификатор не может состоять только из нулей")
    digits = list(map(int, value))

    def check(weights: list[int]) -> int:
        return sum(d * w for d, w in zip(digits, weights)) % 11 % 10

    match len(value):
        case 10:
            valid = check([2, 4, 10, 3, 5, 9, 4, 6, 8]) == digits[9]
            kind = "inn_legal"
        case 12:
            valid = (check([7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == digits[10]
                     and check([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == digits[11])
            kind = "inn_individual"
        case 13:
            valid = int(value[:-1]) % 11 % 10 == digits[-1]
            kind = "ogrn"
        case 15:
            valid = int(value[:-1]) % 13 % 10 == digits[-1]
            kind = "ogrnip"
    if not valid:
        raise ValueError("Неверная контрольная цифра ИНН/ОГРН")
    return value, kind
