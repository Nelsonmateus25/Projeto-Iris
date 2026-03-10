"""
Módulo de Utilitários para Processamento de Texto.

Este módulo contém funções para analisar e extrair informações
de textos de decretos que já foram delimitados por página.

Este arquivo é projetado para ser importado por outros scripts.

Funções principais:
- extrair_blocos_decreto: Divide o texto completo em blocos de decretos.
- classificar_blocos: Classifica cada bloco em 'Tipo 1', 'Tipo 2', ou 'Tipo 3'.
- contar_ocorrencias_vigencia: Conta frases específicas no texto.
"""

import re
from typing import List, Tuple
from collections import Counter


def extrair_blocos_decreto(texto_completo: str) -> List[str]:
    """
    Extrai blocos de texto que contêm "DECRETA", "DECRETAI" ou "DECRETAL",
    com base em delimitadores de página.
    """

    # Regex para encontrar *qualquer* delimitador de página
    delimiter_regex = r"\[(?:INÍCIO|FIM) PAGINA \d+\](?:\[(?:INÍCIO|FIM) PAGINA \d+\])*"
    all_delimiters = list(re.finditer(delimiter_regex, texto_completo))

    # Regex para encontrar a palavra-chave (flexível)
    decretas = list(re.finditer(r"\bDECRETA[IL]?\b", texto_completo))

    if not all_delimiters or not decretas:
        print("Não foram encontrados delimitadores ou a palavra 'DECRETA*'.")
        return []

    # Mapeia cada "DECRETA*" ao seu delimitador de início
    start_delimiters_matches = []
    for decreta_match in decretas:
        latest_delim = None
        for delim_match in all_delimiters:
            if delim_match.start() < decreta_match.start():
                latest_delim = delim_match
            else:
                break

        if latest_delim and latest_delim not in start_delimiters_matches:
            start_delimiters_matches.append(latest_delim)

    # Fatie o texto com base nos delimitadores de início
    blocos = []
    num_blocos = len(start_delimiters_matches)

    for i in range(num_blocos):
        start_match = start_delimiters_matches[i]
        start_pos = start_match.start()

        # Define a posição final padrão como o fim do texto
        end_pos = len(texto_completo)

        if i + 1 < num_blocos:
            # Se não for o último bloco, ele termina onde o *próximo*
            # bloco de decreto começa.
            end_match = start_delimiters_matches[i+1]
            end_pos = end_match.start()

        else:
            # Se for o último bloco, ele NÃO deve parar no próximo
            # delimitador. Vai até o fim do arquivo.
            pass

        bloco_texto = texto_completo[start_pos:end_pos]
        blocos.append(bloco_texto.strip())

    return blocos


def extrair_blocos_decreto_com_paginas(texto_completo: str) -> List[Tuple[str, List[int]]]:
    """
    Versão estendida de `extrair_blocos_decreto` que, além do texto de cada bloco,
    também retorna a lista de páginas (números inteiros) que compõem o bloco.

    A lógica de corte é a mesma: cada bloco começa em um delimitador de página
    imediatamente anterior a uma ocorrência de \"DECRETA*\" e termina onde o próximo
    bloco começa (ou no fim do texto).
    """

    delimiter_regex = r"\[(?:INÍCIO|FIM) PAGINA \d+\](?:\[(?:INÍCIO|FIM) PAGINA \d+\])*"
    all_delimiters = list(re.finditer(delimiter_regex, texto_completo))

    decretas = list(re.finditer(r"\bDECRETA[IL]?\b", texto_completo))

    if not all_delimiters or not decretas:
        print("Não foram encontrados delimitadores ou a palavra 'DECRETA*'.")
        return []

    start_delimiters_matches = []
    decreta_positions: List[int] = []
    for decreta_match in decretas:
        latest_delim = None
        for delim_match in all_delimiters:
            if delim_match.start() < decreta_match.start():
                latest_delim = delim_match
            else:
                break

        if latest_delim and latest_delim not in start_delimiters_matches:
            start_delimiters_matches.append(latest_delim)
            decreta_positions.append(decreta_match.start())

    blocos_com_paginas: List[Tuple[str, List[int]]] = []
    num_blocos = len(start_delimiters_matches)

    # Regex específico para capturar apenas INÍCIO de página com número
    regex_inicio_pagina = re.compile(
        r"\[INÍCIO\s+PAGINA\s+(\d+)\]", re.IGNORECASE
    )

    for i in range(num_blocos):
        start_match = start_delimiters_matches[i]
        start_pos = start_match.start()

        end_pos_conteudo = len(texto_completo)
        end_pos_paginas = len(texto_completo)
        if i + 1 < num_blocos:
            end_pos_conteudo = start_delimiters_matches[i + 1].start()
            # Usa posição do próximo DECRETA* para evitar incluir [INÍCIO PAGINA]
            # da primeira página do próximo decreto na contagem do bloco atual
            end_pos_paginas = decreta_positions[i + 1]

        trecho_bloco = texto_completo[start_pos:end_pos_conteudo]
        trecho_para_paginas = texto_completo[start_pos:end_pos_paginas]

        # Coleta todos os INÍCIO PAGINA dentro do trecho limitado ao próximo DECRETA
        paginas_bloco: List[int] = []
        for m in regex_inicio_pagina.finditer(trecho_para_paginas):
            try:
                paginas_bloco.append(int(m.group(1)))
            except ValueError:
                continue

        blocos_com_paginas.append((trecho_bloco.strip(), paginas_bloco))

    return blocos_com_paginas


def classificar_blocos(blocos: List[str]) -> List[Tuple[int, str, str]]:
    """
    Recebe uma lista de blocos de texto e classifica cada um
    com base nos padrões de cabeçalho e conteúdo interno.
    """

    # --- REGEX COMPILADOS ---

    # Padrão 1: "DECRETO Nro XXXXX/XX" (Genérico)
    padrao_validacao_tipo1 = re.compile(
        r"""
        (?:DECRETO|Decreto)\s+
        N(?:ro|o|º|o\.|º\.|°)?\s*[\.:]?\s*
        \d+
        (?:[\./\-]\d+)*
        """,
        re.IGNORECASE | re.VERBOSE
    )

    # Padrão 2 (Cabeçalho): Verifica se "Decreto Orçamentário N°..." existe
    padrao2_cabecalho = re.compile(
        r"Decreto Orçamentário N°\s*\d+\s*/?\s*\d+",
        re.IGNORECASE
    )

    # Padrão 2 (Rodapé): Verifica se o Art. 3° específico existe
    padrao2_rodape = re.compile(
        r"Art\.\s*3°\.\s+Este\s+decreto\s+entrará\s+em\s+vigor\s+na\s+data\s+de\s+sua\s+publicação,\s+revogada\s+as\s+disposições\s+em\s+contrário\.",
        re.IGNORECASE | re.DOTALL
    )

    # Padrão 3 (Anexo III): Identifica a presença explícita do Anexo 3
    # Usamos (?im) para Case Insensitive + Multiline (âncora ^ funciona por linha)
    padrao_anexo_3 = re.compile(
        r'(?im)^[\s\t]*ANEXO\s+(?:III|3)(?![a-zA-Z0-9])'
    )

    resultados = []
    for i, bloco in enumerate(blocos):

        tipo = "Tipo Desconhecido"  # Começa como desconhecido

        # ORDEM DE PRECEDÊNCIA:

        # 1. Testa o Padrão 2 (Mais específico e estrutura rígida)
        if padrao2_cabecalho.search(bloco) and padrao2_rodape.search(bloco):
            tipo = "Tipo 2 (Decreto Orçamentário)"

        # 2. Testa o Padrão 3 (Presença de Anexo III)
        # Importante testar ANTES do Tipo 1, pois um Tipo 3 também se parece com Tipo 1 no cabeçalho.
        elif padrao_anexo_3.search(bloco):
            tipo = "Tipo 3 (Anexo III)"

        # 3. Testa o Padrão 1 (Genérico)
        elif padrao_validacao_tipo1.search(bloco):
            tipo = "Tipo 1 (Decreto Nro)"

        resultados.append((i + 1, tipo, bloco))

    return resultados


def identificar_padrao_global(texto_completo: str) -> str:
    """
    Analisa o texto completo do arquivo para determinar um ÚNICO padrão
    de processamento para todos os decretos contidos nele.

    Ordem de Prioridade e Lógica:
    1. P3: Presença de 'ANEXO III' ou 'ANEXO 3'.
    2. P2: Assinatura de Decreto Orçamentário (Cabeçalho, Rodapé ou termos 'REDU.').
    3. P1: Presença genérica de 'DECRETO Nº'.
    4. DESCONHECIDO: Se não parecer um arquivo de decretos.
    """

    # ==========================================================================
    # 1. COMPILAÇÃO DE REGEX (Definições)
    # ==========================================================================

    # --- Padrão 3 (Prioridade Máxima) ---
    # Busca por "ANEXO III" ou "ANEXO 3" isolado na linha (Início de linha ^)
    regex_p3 = re.compile(
        r'(?im)^[\s\t]*ANEXO\s+(?:III|3)(?![a-zA-Z0-9])'
    )

    # --- Padrão 2 (Sinais Fortes) ---
    # Sinal 1: Título explícito
    regex_p2_titulo = re.compile(
        r"Decreto Orçamentário",
        re.IGNORECASE
    )

    # Sinal 2: Termo técnico "REDU." (Redução de dotação)
    regex_p2_redu = re.compile(
        r"REDU\.",
        re.IGNORECASE
    )

    # Sinal 3: Rodapé específico (Fim do decreto orçamentário padrão)
    regex_p2_rodape = re.compile(
        r"Art\.\s*3[º°]\.\s*Este\s+decreto\s+entrará\s+em\s+vigor",
        re.IGNORECASE
    )

    # --- Padrão 1 (Genérico / Fallback) ---
    # Verifica se existe pelo menos a palavra DECRETO seguida de número
    regex_p1_generico = re.compile(
        r"(?:DECRETO|Decreto)\s+N(?:ro|o|º|o\.|º\.|°)?",
        re.IGNORECASE
    )

    # ==========================================================================
    # 2. LÓGICA DE DECISÃO (Hierarquia)
    # ==========================================================================

    # --- NÍVEL 1: Verifica Padrão 3 (Anexo III) ---
    # Se houver UM único Anexo III, assumimos que a estrutura exige o processador P3
    # para tratar a extração de fontes complexas.
    if regex_p3.search(texto_completo):
        return "P3"

    # --- NÍVEL 2: Verifica Padrão 2 (Orçamentário) ---
    # Para ser robusto, verificamos se satisfaz pelo menos UMA das condições fortes:
    # A. Tem o título "Decreto Orçamentário"
    # B. Tem o rodapé específico do Art 3.
    # C. Tem mais de uma ocorrência de "REDU." (Evita falso positivo se aparecer só uma vez por erro de OCR)

    tem_titulo_p2 = regex_p2_titulo.search(texto_completo)
    tem_rodape_p2 = regex_p2_rodape.search(texto_completo)
    contagem_redu = len(regex_p2_redu.findall(texto_completo))

    if tem_titulo_p2 or tem_rodape_p2 or contagem_redu >= 1:
        return "P2"

    # --- NÍVEL 3: Verifica Padrão 1 (Genérico) ---
    # Se não for P3 nem P2, verificamos se é, de fato, um decreto.
    if regex_p1_generico.search(texto_completo):
        return "P1"

    # --- NÍVEL 4: Desconhecido ---
    # O arquivo não parece conter decretos válidos.
    return "DESCONHECIDO"


def contar_ocorrencias_vigencia(texto_completo: str) -> int:
    """
    Conta o número de ocorrências da frase "Este Decreto entrará".
    """
    # \s+ significa "um ou mais caracteres de espaço"
    padrao_vigencia = re.compile(r"Este\s+Decreto\s+entrará", re.IGNORECASE)

    matches = padrao_vigencia.findall(texto_completo)

    return len(matches)
