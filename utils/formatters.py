"""
Módulo de funções utilitárias de formatação.

Centraliza as funções formatar_valor e formatar_data que eram duplicadas
em P1Processor, P2Processor e P3Processor (princípio DRY).
"""

import re
from datetime import datetime


def formatar_valor(valor_str: str | None) -> str | None:
    """
    Converte uma string de valor monetário para o formato brasileiro.
    Ex: "1234567.89" -> "1.234.567,89"
    Ex: "1.234.567,89" -> "1.234.567,89" (já formatado, mantém)
    Retorna None se a string estiver vazia ou for inválida.
    """
    if valor_str is None or not isinstance(valor_str, str):
        return valor_str
    valor_limpo = re.sub(r'[^\d,\.]', '', valor_str)
    if not valor_limpo:
        return None
    try:
        if ',' in valor_limpo and '.' in valor_limpo:
            valor_limpo = valor_limpo.replace('.', '').replace(',', '.')
        else:
            valor_limpo = valor_limpo.replace(',', '.')
        valor_float = float(valor_limpo)
        return f"{valor_float:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (ValueError, TypeError):
        return valor_str


def formatar_data(data_str: str | None) -> str | None:
    """
    Converte uma string de data para o formato por extenso em português.
    Aceita dois formatos de entrada:
      - "dd/mm/yyyy"          -> "1 de Janeiro de 2023"
      - "dd de Mês de yyyy"   -> normaliza capitalização
    Retorna a string original se não reconhecer o formato.
    """
    if data_str is None or not isinstance(data_str, str):
        return data_str
    data_str = data_str.strip()
    # Padrão dd/mm/yyyy
    try:
        dt_obj = datetime.strptime(data_str, '%d/%m/%Y')
        return dt_obj.strftime('%d de %B de %Y').replace(
            dt_obj.strftime('%B'), dt_obj.strftime('%B').capitalize()
        )
    except ValueError:
        pass
    # Padrão "dd de Mês de yyyy" (qualquer capitalização) — normaliza
    match = re.match(r'^(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})\s*$', data_str, re.IGNORECASE)
    if match:
        dia, mes, ano = match.group(1), match.group(2).capitalize(), match.group(3)
        return f"{dia} de {mes} de {ano}"
    return data_str
