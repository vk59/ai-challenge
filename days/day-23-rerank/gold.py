#!/usr/bin/env python3
"""Что считать «золотым» куском (день 23).

Отдельный файл, а не функция в evaluate.py, по скучной причине: в дне 22
файл тоже называется evaluate.py, и когда обе папки лежат в sys.path,
`from evaluate import ...` достаётся тому, кто в пути первым. Имя gold.py
уникально, и спорить не о чем.

Золотой кусок — тот, который и из нужного файла, и содержит ожидаемую
подстроку. Проверять только файл слишком мягко: в дне 22 нужный файл стоял
первым в выдаче, а раздел с ответом — восьмым, и ответа модель не получила.
"""


def is_gold(chunk, item) -> bool:
    """chunk — кусок из индекса, item — запись из контрольного набора."""
    low = (chunk.text or "").lower()
    if chunk.source not in item["sources"]:
        return False
    return all(any(variant.lower() in low for variant in variants)
               for variants in item["expect"])


__all__ = ["is_gold"]
