"""
Módulo de Processamento para Decretos "Padrão 1".

Este módulo contém a classe `P1Processor`, que é especializada em
realizar a extração de dados Textual de blocos de texto
que já foram classificados como "Tipo 1".

A classe depende do PyMuPDF (fitz) para a extração de imagens de PDF.

"""

import re
import os
import tempfile
import json
import time
import locale
import logging
from typing import List, Optional, Set, Tuple, Dict, Any
import fitz  
import google.generativeai as genai
from utils.formatters import formatar_valor, formatar_data
logger = logging.getLogger(__name__)


class P1Processor:
    """
    Processador especializado para Decretos Padrão 1 (Decreto Nro X/XXXX).
    Esta classe encapsula toda a lógica de extração de dados.
    """

    def __init__(self, gemini_model: genai.GenerativeModel):
        self.model = gemini_model
        try:
            locale.setlocale(locale.LC_TIME, 'pt_BR.UTF-8')
        except locale.Error:
            logger.warning("Locale 'pt_BR.UTF-8' não encontrado. Usando locale padrão.")

        # --- PROMPTS Específicos do Padrão 1 ---
        # (Prompts originais do P1Processor mantidos)

        self.PROMPT_ANALISE_Pattern_1 = """
        ### TAREFA ###
        Sua tarefa é analisar o texto de UM ÚNICO decreto e extrair as informações solicitadas em formato JSON, seguindo TODAS as regras e HIERARQUIAS rigorosamente.

        ### HIERARQUIA DE BUSCA DE VALORES PARA FONTES ###
        Para determinar o `"valor"` de cada fonte de recurso (ex: "Anulação de Dotações", "Excesso de Arrecadação"), siga esta ordem de prioridade:
        1. **Busca (Valor Explícito no Texto):** Procure no texto do Artigo 2º por um valor monetário EXPLICITAMENTE associado ao nome da fonte (ex: "...à conta de Excesso de Arrecadação R$ 3.932.927,00...").
        2.  **Regra Final:** Se uma fonte for mencionada mas for impossível encontrar um valor explícito, o campo `"valor"` deve ser `null`. (ex: "... e Anulação parcial e/ou total da(s) seguinte(s) dotação(ões) orçamentária(s): ..." mas não houver valore vinculado a anulação desconsidere.) **NÃO FAÇA CÁLCULOS.**

        ### INSTRUÇÕES DE EXTRAÇÃO ###
        - `"numero"`: O número do decreto, extraído do título.
        - `"data"`: A data do decreto, extraída do título.
        - `"valor_total"`: O valor total do Artigo 1º.
        - `"tipo_credito"`: O tipo de crédito adicional do Artigo 1º que pode ser "Suplementar", "Especial" ou "Extraordinário". (Normalize para conter "Créditos" e o tipo: exemplo: "Créditos Suplementares")

        - `"fontes_detalhadas"`: Uma lista de objetos. Crie um objeto apenas para as fontes de recurso do Artigo 2º que possuam um valor monetário (R$) explicitamente associado no texto. Ignore fontes que são apenas mencionadas de forma genérica sem detalhamento financeiro.
            - `"fonte"`: O nome da fonte de recursos, que pode ser "Superávit Financeiro", "Excesso de Arrecadação", "Anulação de Dotações" e/ou "Operações de Crédito". (Normalize "Anulações..." para "Anulação de Dotações"). Ex: " ... decorrerão de anulações parciais das dotações orçamentárias indicada ..."-> "Anulação de Dotações"
            Para determinar a fonte de recurso verifique o Artigo 2º. 
                - exemplo: "... decorrerão de anulações parciais das dotações orçamentárias ..."; ** FONTE: "Anulação de Dotações" **
                - exemplo: "... decorrerão de excesso de arrecadação"; ** FONTE: "Excesso de Arrecadação" **
                - (exemplo: "... decorrerão do superávit financeiro apurado"; ** FONTE: "Superávit Financeiro" ** 
                Se houver mais de uma fonte de recurso estará explicito no Artigo 2º. 

            - `"valor"`: O valor específico da fonte, encontrado seguindo a HIERARQUIA DE BUSCA acima.
            - `"codigos"`: Deixe vazio por enquanto.
            
        ### FORMATO DE SAÍDA ###
        {
          "numero": "string", "data": "string", "valor_total": "string", "tipo_credito": "string",
          "fontes_detalhadas": [{"fonte": "string", "valor": "string ou null", "codigos": []}]
        }
        """


        # --- PADRÕES DO TESTHELPER 

        regex_limpo_1 = r'^[\s\t]*ANEXO\s+[I1L,aT]a?(?![a-zA-Z0-9])'
        regex_limpo_2 = r'^[\s\t]*ANEXO\s+([IT]{2}|2|[I][\s,]*2|[I][L])(?![a-zA-Z0-9])'

        self.PADRAO_LIMPO_ANEXO_1 = re.compile(
            regex_limpo_1,
            re.IGNORECASE | re.MULTILINE
        )
        self.PADRAO_LIMPO_ANEXO_2 = re.compile(
            regex_limpo_2,
            re.IGNORECASE | re.MULTILINE
        )

        self.PADRAO_ANEXO_GENERICO = re.compile(
            r'^[\s\t]*ANEXO\b',
            re.MULTILINE
        )

        self.PADRAO_TODOS_CONHECIDOS = re.compile(
            f"({regex_limpo_1})|({regex_limpo_2})",
            re.IGNORECASE | re.MULTILINE
        )

        self.PADRAO_TAG_PAGINA = re.compile(
            r'\[INÍCIO\s+PAGINA\s+\d+\]',
            re.IGNORECASE
        )
        self.PADRAO_ITERAR_PAGINA = re.compile(
            r'\[INÍCIO\s+PAGINA\s+(\d+)\]([\s\S]*?)(?=\[INÍCIO\s+PAGINA|\Z)',
            re.IGNORECASE
        )
        self.PADRAO_EXCESSO = re.compile(
            r'(Exce\.\s*arrec\.)',
            re.IGNORECASE
        )

        # 2. Padrão para "Interrupções Permitidas" (NÃO quebram um grupo)
        self.PADRAO_INTERRUPCAO_PERMITIDA = re.compile(
            # Corresponde a 'Anul.dotação' OU uma linha que é SÓ códigos/números
            r'(Anul\.\s*dotação|^\s*[\d\.\s]+\s*$)',
            re.IGNORECASE
        )

    # --- Métodos de Formatação e Chamada de API ---

    def _criar_lotes(self, paginas: List[int], tamanho_lote: int) -> List[List[int]]:
        """Divide uma lista de páginas em lotes (chunks) de tamanho fixo."""
        lotes = []
        if not paginas or tamanho_lote <= 0:
            return []
        for i in range(0, len(paginas), tamanho_lote):
            lotes.append(paginas[i:i + tamanho_lote])
        return lotes

    def _post_processar_dados(self, dados_json: dict) -> dict:
        if not dados_json:
            return dados_json
        dados_json['data'] = formatar_data(dados_json.get('data'))
        dados_json['valor_total'] = formatar_valor(dados_json.get('valor_total'))
        if 'fontes_detalhadas' in dados_json and isinstance(dados_json['fontes_detalhadas'], list):
            for detalhe in dados_json['fontes_detalhadas']:
                detalhe['valor'] = formatar_valor(detalhe.get('valor'))
        return dados_json

    def _limpar_texto_ocr(self, texto: str) -> str:
        texto = re.sub(r'\s{2,}', ' ', texto)
        texto = re.sub(r'\s*\n\s*', '\n', texto)
        return texto.strip()

    def _call_gemini(self, prompt: str, contexto: str, imagens: Optional[List[bytes]] = None, tentativa=1, max_tentativas=3) -> dict | None:
        if tentativa > max_tentativas:
            logger.error("Máximo de tentativas (%d) atingido.", max_tentativas)
            return None
        try:
            generation_config = genai.GenerationConfig(
                response_mime_type="application/json")

            conteudo_prompt = []

            if imagens:
                for img_bytes in imagens:
                    conteudo_prompt.append({
                        "inline_data": {
                            "data": img_bytes,
                            "mime_type": "image/jpeg"
                        }
                    })

            contexto_limpo = self._limpar_texto_ocr(contexto)
            texto_completo = prompt + '\n\n--- CONTEÚDO PARA ANÁLISE ---\n' + contexto_limpo
            conteudo_prompt.append(texto_completo)

            resposta_objeto = self.model.generate_content(
                conteudo_prompt,
                generation_config=generation_config
            )

            metadata = resposta_objeto.usage_metadata
            logger.info(
                "[TOKENS] Entrada: %d | Saída: %d | Total: %d",
                metadata.prompt_token_count,
                metadata.candidates_token_count,
                metadata.total_token_count,
            )
            return json.loads(resposta_objeto.text)

        except Exception as e:
            logger.error("Erro na chamada da API ou JSON (Tentativa %d): %s", tentativa, e)
            time.sleep(5)
            return self._call_gemini(prompt, contexto, imagens, tentativa + 1, max_tentativas)

    # --- Métodos de Segmentação P1 (Lógica de segmentation.py) ---

    def _contar_ocr_simples(self, texto_anexo: str) -> int:
        """
        [Verificação Nível 1]
        Conta TODAS as ocorrências de 'Exce.arrec.' no texto.
        Este número deve ser comparado ao total de códigos ACUMULADOS.
        Ex: 65
        """
        if not texto_anexo:
            return 0
        return len(self.PADRAO_EXCESSO.findall(texto_anexo))

    def _contar_grupos_de_excesso(self, texto_anexo: str) -> int:
        """
        [Verificação Nível 2 - Robusta]
        Conta "grupos" de Excesso de Arrecadação.
        """
        if not texto_anexo:
            return 0

        grupo_count = 0
        in_group = False  # Estamos atualmente dentro de um grupo?

        for line in texto_anexo.splitlines():
            line_limpa = line.strip()
            if not line_limpa:
                continue  # Pula linhas em branco

            # 1. A linha é "Exce.arrec."?
            if self.PADRAO_EXCESSO.search(line_limpa):
                if not in_group:
                    # É a primeira linha de um NOVO grupo
                    grupo_count += 1
                    in_group = True
                # Se in_group já era True, apenas continuamos no mesmo grupo.

            # 2. A linha é uma interrupção permitida?
            elif in_group and self.PADRAO_INTERRUPCAO_PERMITIDA.search(line_limpa):
                # Estamos em um grupo e encontramos Anulação ou um Código.
                # Isso NÃO quebra o grupo. Apenas continuamos.
                pass

            # 3. A linha é um texto "real" que quebra o grupo?
            else:
                # A linha é um texto normal (ex: "TOTAL Sec...")
                # Isso quebra o grupo.
                in_group = False

        return grupo_count

    def _ajustar_corte_por_pagina(self, bloco_inteiro: str, indice_corte: int) -> Tuple[str, str]:
        """ (Lógica do TestHelper) """
        indice_tag_anterior = 0
        for match_tag in self.PADRAO_TAG_PAGINA.finditer(bloco_inteiro[:indice_corte]):
            indice_tag_anterior = match_tag.start()
        bloco_1 = bloco_inteiro[:indice_tag_anterior].strip()
        bloco_2 = bloco_inteiro[indice_tag_anterior:].strip()
        return bloco_1, bloco_2

    def _segmentar_bloco_decreto(self, bloco_decreto: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        (Lógica do TestHelper: VETO DE AMBIGUIDADE + LÓGICA 100% LIMPA)
        """

        # --- ETAPA 1: VETO DE AMBIGUIDADE (MODIFICADO) ---

        # Procura o Anexo II para definir um "limite" para a verificação de veto.
        # Qualquer ANEXO desconhecido (como ANEXO M) APÓS o Anexo II
        # é irrelevante para esta segmentação.
        match_anexo_2_limite = self.PADRAO_LIMPO_ANEXO_2.search(bloco_decreto)

        texto_para_veto = bloco_decreto
        if match_anexo_2_limite:
            # Se o Anexo II existir, SÓ NOS PREOCUPAMOS com a ambiguidade
            # ANTES e ATÉ O FIM da tag do Anexo II.
            # O "ANEXO M" que vier depois será ignorado pelo veto.
            texto_para_veto = bloco_decreto[:match_anexo_2_limite.end()]

        # (O restante da lógica de veto original agora roda no 'texto_para_veto',
        # que é um texto "truncado" e mais seguro)
        matches_genericos = list(
            self.PADRAO_ANEXO_GENERICO.finditer(texto_para_veto)  # MODIFICADO
        )
        posicoes_conhecidas = {
            match.start()
            # MODIFICADO
            for match in self.PADRAO_TODOS_CONHECIDOS.finditer(texto_para_veto)
        }

        num_genericos = len(matches_genericos)
        num_conhecidos = 0

        for match in matches_genericos:
            if match.start() in posicoes_conhecidas:
                num_conhecidos += 1

        if num_genericos > num_conhecidos:
            corpo_alerta = (
                f"[ALERTA_AMBIGUIDADE] Este bloco contém {num_genericos} "
                f"ocorrência(s) de 'ANEXO' (no escopo de verificação), mas apenas {num_conhecidos} "
                "puderam ser classificada(s) como Anexo 1 ou 2 (Limpo). "
                "Extração de anexos VETADA para este bloco."
            )
            return (corpo_alerta, None, None)

        # --- ETAPA 2: LÓGICA 100% LIMPA (Se passou no Veto) ---
        # (O restante da função permanece EXATAMENTE igual)
        corpo = bloco_decreto
        anexo_1 = None
        anexo_2 = None

        match_anexo_1 = self.PADRAO_LIMPO_ANEXO_1.search(corpo)

        if not match_anexo_1:
            return (corpo.strip(), None, None)

        corpo, resto_bloco = self._ajustar_corte_por_pagina(
            corpo, match_anexo_1.start()
        )

        match_anexo_2 = self.PADRAO_LIMPO_ANEXO_2.search(resto_bloco)

        if not match_anexo_2:
            anexo_1 = resto_bloco
            return (corpo, anexo_1, None)

        anexo_1, anexo_2 = self._ajustar_corte_por_pagina(
            resto_bloco, match_anexo_2.start()
        )

        return (corpo, anexo_1, anexo_2)

    def _mapear_anexo_por_continuidade(self, bloco_anexo_1: str) -> List[int]:
        """
        (Lógica do TestHelper: BUFFER DE 1 PÁGINA LIMPO)
        """
        if not bloco_anexo_1:
            return []

        paginas_validas: List[int] = []
        matches_pagina = list(
            self.PADRAO_ITERAR_PAGINA.finditer(bloco_anexo_1))

        for i, match in enumerate(matches_pagina):
            try:
                num_pagina = int(match.group(1))
                conteudo_pagina = match.group(2)
            except (ValueError, IndexError):
                continue

            # 1. PASSE LIVRE
            if i == 0:
                paginas_validas.append(num_pagina)
                continue

            # 2. TESTE DE CONTINUIDADE NORMAL (APENAS LIMPO, início de linha)
            if self.PADRAO_LIMPO_ANEXO_1.search(conteudo_pagina):
                paginas_validas.append(num_pagina)
                continue

            # 3. LÓGICA DE BUFFER (SE O TESTE 2 FALHOU)
            if i + 1 < len(matches_pagina):
                proximo_match = matches_pagina[i+1]
                try:
                    conteudo_proxima_pagina = proximo_match.group(2)
                except IndexError:
                    break

                # Testa a próxima página (APENAS LIMPO, início de linha)
                if self.PADRAO_LIMPO_ANEXO_1.search(conteudo_proxima_pagina):
                    paginas_validas.append(num_pagina)
                    continue

            # 4. CORTE REAL
            break

        return paginas_validas

    # --- Métodos de Extração de Mídia (PyMuPDF) ---

    def _extract_text_from_pages(self, full_ocr_text: str, page_numbers: List[int]) -> str:
        """Extrai o conteúdo de texto do OCR apenas para as páginas especificadas."""
        if not page_numbers:
            return ""
        page_numbers.sort()
        padrao_pagina_e_conteudo = re.compile(
            r'\[INÍCIO\s+PAGINA\s+(\d+)\]([\s\S]*?)(?=\[INÍCIO\s+PAGINA|\Z)',
            re.IGNORECASE
        )
        texto_filtrado = []
        matches = padrao_pagina_e_conteudo.finditer(full_ocr_text)
        for match in matches:
            try:
                num_pagina = int(match.group(1))
            except ValueError:
                continue
            if num_pagina in page_numbers:
                texto_filtrado.append(match.group(0))
        return "\n".join(texto_filtrado)

    def _extrair_paginas_em_imagem(self, caminho_pdf: str, paginas: List[int]) -> List[Dict[str, Any]]:
        """
        Extrai as páginas de um PDF em formato de imagem (bytes) usando PyMuPDF (fitz).
        """
        imagens_paginas = []
        # PyMuPDF é 0-indexed
        indices_paginas = [p - 1 for p in paginas if p > 0]

        try:
            if not os.path.exists(caminho_pdf):
                logger.error("[PyMuPDF] Arquivo PDF não encontrado em: %s", caminho_pdf)
                return []

            documento = fitz.open(caminho_pdf)

            for indice, numero_pagina in zip(indices_paginas, paginas):
                if indice < len(documento):
                    page = documento.load_page(indice)
                    pix = page.get_pixmap(matrix=fitz.Matrix(150/72, 150/72))
                    image_bytes = pix.tobytes("jpeg")
                    imagens_paginas.append({
                        'page_num': numero_pagina,
                        'image_bytes': image_bytes
                    })
                else:
                    logger.warning("[PyMuPDF] Página %d fora do limite do PDF.", numero_pagina)
            documento.close()
        except Exception as e:
            logger.error("[PyMuPDF] Falha ao extrair imagens do PDF: %s", e)
            return []

        return imagens_paginas



    def processar_bloco(
        self,
        bloco_tipo_1: str,
        texto_total_ocr: str,
        caminho_pdf_associado: str
    ) -> Optional[Dict[str, Any]]:
        """
        Método principal da classe.
        Processa UM ÚNICO bloco de texto (já classificado como Tipo 1).
        Extrai informações textuais 
        """
        logger.info("--- Processando Bloco P1 ---")

        # 1. Segmentação e VETO (Lógica do TestHelper)
        bloco_inicio_ajustado, bloco_meio_anexo, _ = self._segmentar_bloco_decreto(
            bloco_tipo_1
        )

        # 2. VERIFICAÇÃO DO VETO (LÓGICA INTEGRADA)
        if bloco_inicio_ajustado and bloco_inicio_ajustado.startswith("[ALERTA_AMBIGUIDADE]"):
            logger.warning(bloco_inicio_ajustado)
            logger.warning("--- Bloco P1 VETADO. ---")
            return {"ERRO": "Veto de Ambiguidade", "detalhe": bloco_inicio_ajustado}

        if not bloco_inicio_ajustado:
            logger.error("[P1] Falha na segmentação. Bloco de início está vazio.")
            return {"ERRO": "Falha na Segmentação", "detalhe": "Bloco de início (corpo) não encontrado."}

        # 3. Extração do Corpo (Etapa 1 - Gemini) - Textual
        logger.info("Etapa 1: Executando extração primária do Corpo do Decreto (Textual)...")
        dados_iniciais = self._call_gemini(
            self.PROMPT_ANALISE_Pattern_1, bloco_inicio_ajustado)

        if not dados_iniciais:
            logger.error("[P1] Falha na extração primária. Pulando.")
            return {"ERRO": "Falha na API Gemini", "detalhe": "A extração textual primária falhou ou retornou vazio."}

        # 4. (Desativado) Extração Multimodal de códigos de Excesso de Arrecadação.
        # A partir de agora, não extraímos mais códigos; apenas utilizamos as
        # fontes textuais para identificar se o decreto é de Excesso de Arrecadação.

        # 5. Pós-processamento e Formatação Final
        logger.info("Etapa 2: Aplicando formatação final...")
        dados_finais_formatados = self._post_processar_dados(dados_iniciais)

        # Flag auxiliar para a camada de apresentação: decreto com Excesso?
        is_excesso = any(
            isinstance(d, dict) and d.get("fonte") == "Excesso de Arrecadação"
            for d in dados_finais_formatados.get("fontes_detalhadas", [])
        )
        dados_finais_formatados["_is_excesso"] = is_excesso

        logger.info("--- Bloco P1 finalizado. ---")

        return dados_finais_formatados
