"""
Módulo de Processamento para Decretos "Padrão 3".

Este módulo contém a classe `P3Processor`, especializada em realizar a 
extração de dados de blocos de texto classificados como "Tipo 3".

Lógica de Segmentação Específica:
1. Parte A (Contexto): Do início até o Artigo 3º (ou Anexo 1 como fallback).
2. Parte B (Dados Financeiros): Do Anexo 3 até o final.
3. Mesclagem e Envio para LLM.
"""

import re
import json
import time
import locale
import logging
from typing import List, Optional, Tuple, Dict, Any
import google.generativeai as genai
from utils.formatters import formatar_valor, formatar_data

logger = logging.getLogger(__name__)




class P3Processor:
    """
    Processador especializado para Decretos Padrão 3 (Com Anexo III).
    """

    def __init__(self, gemini_model: genai.GenerativeModel):
        self.model = gemini_model
        try:
            locale.setlocale(locale.LC_TIME, 'pt_BR.UTF-8')
        except locale.Error:
            pass

        # --- REGEX PATTERNS ---

        # Artigo 3: Aceita "Art. 3", "Art. 3º", "Art 3", etc.
        self.REGEX_ART_3 = re.compile(r'Art[\.\s]*3[º°\.]?', re.IGNORECASE)

        # Anexo 1: Aceita "ANEXO I", "ANEXO 1" (Ancorado no início da linha)
        self.REGEX_ANEXO_1 = re.compile(
            r'(?im)^[\s\t]*ANEXO\s+(I|1)(?![I0-9])')

        # Anexo 3: Aceita "ANEXO III", "ANEXO 3" (Ancorado no início da linha)
        self.REGEX_ANEXO_3 = re.compile(
            r'(?im)^[\s\t]*ANEXO\s+(?:III|3)(?![a-zA-Z0-9])')

        # --- PROMPT ---
        self.PROMPT_ANALISE_Pattern_3 = """
        ### TAREFA ###
        Sua tarefa é analisar o texto de UM ÚNICO decreto e extrair as informações solicitadas em formato JSON, seguindo TODAS as regras e HIERARQUIAS rigorosamente.

        ### REGRAS CRÍTICAS ###
        - **PROIBIDO CALCULAR:** Extraia apenas valores explícitos. Se não houver valor vinculado diretamente à fonte, retorne `null`.

        ### HIERARQUIA DE BUSCA PARA FONTES ###
        Para determinar a fonte de recurso verifique o Artigo 2º. 
        - exemplo: "... decorrerão de anulações parciais das dotações orçamentárias ..."; ** FONTE: "Anulação de Dotações" **
        - exemplo: "... decorrerão de excesso de arrecadação"; ** FONTE: "Excesso de Arrecadação" **
        - (exemplo: "... decorrerão do superávit financeiro apurado"; ** FONTE: "Superávit Financeiro" **
        Se houver mais de uma fonte de recurso estará explicito no Artigo 2º. 
        
        ### INSTRUÇÕES DE EXTRAÇÃO ###
        - `"numero"`: O número do decreto, extraído do título.
        - `"data"`: A data do decreto, extraída do título.
        - `"valor_total"`: O valor total do Artigo 1º.
        - `"tipo_credito"`: O tipo de crédito adicional do Artigo 1º que pode ser "Suplementar", "Especial" ou "Extraordinário". (Normalize para conter "Créditos" e o tipo: exemplo: "Créditos Suplementares")
        

        - `"fontes_detalhadas"`: Uma lista de objetos. Crie um objeto apenas para as fontes de recurso do Artigo 2º que possuam um valor monetário (R$) explicitamente associado no texto. Ignore fontes que são apenas mencionadas de forma genérica sem detalhamento financeiro.
            - `"fonte"`: O nome da fonte de recursos, que pode ser "Superávit Financeiro", "Excesso de Arrecadação", "Anulação de Dotações" e/ou "Operações de Crédito". (Normalize "Anulações..." para "Anulação de Dotações").
            Para determinar a fonte de recurso verifique o Artigo 2º. 
                - exemplo: "... decorrerão de anulações parciais das dotações orçamentárias ..."; ** FONTE: "Anulação de Dotações" **
                - exemplo: "... decorrerão de excesso de arrecadação"; ** FONTE: "Excesso de Arrecadação" **
                - (exemplo: "... decorrerão do superávit financeiro apurado"; ** FONTE: "Superávit Financeiro" ** 
                Se houver mais de uma fonte de recurso estará explicito no Artigo 2º. 
            - `"valor"`: O valor específico da fonte, encontrado seguindo a HIERARQUIA DE BUSCA acima.
            - `"codigos"`: Deixe vazio.
               
        ### FORMATO DE SAÍDA ###
        {
          "numero": "string", "data": "string", "valor_total": "string", "tipo_credito": "string",
          "fontes_detalhadas": [{"fonte": "string", "valor": "string ou null", "codigos": []}]
        }
        """

    def _limpar_texto(self, texto: str) -> str:
        if not texto:
            return ""
        texto = re.sub(r'\s{2,}', ' ', texto)
        return texto.strip()

    # --- Lógica de Segmentação (Regra P3) ---

    def _segmentar_bloco_decreto(self, bloco: str) -> Tuple[str, str, List[str]]:
        """
        Segmenta o bloco conforme regra P3:
        1. Parte A: Início -> Art 3 (ou Anexo 1 ou Tudo).
        2. Parte B: Anexo 3 -> Fim.
        """
        erros = []
        parte_a = ""
        parte_b = ""

        # Encontrar posições
        match_art_3 = self.REGEX_ART_3.search(bloco)
        match_anexo_1 = self.REGEX_ANEXO_1.search(bloco)
        match_anexo_3 = self.REGEX_ANEXO_3.search(bloco)

        # --- 1. DEFINIÇÃO DA PARTE A (Corpo) ---
        if match_art_3:
            # Regra: Captura do começo até o Artigo 3 (inclusivo para pegar a linha do artigo)
            parte_a = bloco[:match_art_3.end()]
        elif match_anexo_1:
            # Fallback 1: Se não tem Art 3, pega até começar o Anexo 1
            parte_a = bloco[:match_anexo_1.start()]
            erros.append(
                "Aviso: 'Art. 3º' não encontrado. Corte realizado no início do 'Anexo I'.")
        else:
            # Fallback 2: Manda o bloco todo como Parte A, mas gera aviso de erro
            parte_a = bloco
            erros.append(
                "Erro Estrutural: 'Art. 3º' e 'Anexo I' não encontrados na Parte A.")

        # --- 2. DEFINIÇÃO DA PARTE B (Fontes / Anexo 3) ---
        if match_anexo_3:
            # Regra: Captura do ANEXO III até o final
            parte_b = bloco[match_anexo_3.start():]
            logger.info("'Anexo III' encontrado.")
        else:
            logger.warning("'Anexo III' NÃO encontrado.")
            # Parte B fica vazia

        return parte_a, parte_b, erros


    def _call_gemini(self, prompt: str, texto_concatenado: str, tentativa=1, max_tentativas=3) -> dict | None:
        if tentativa > max_tentativas:
            return None
        try:
            generation_config = genai.GenerationConfig(
                response_mime_type="application/json")

            texto_limpo = self._limpar_texto(texto_concatenado)
            conteudo_prompt = f"{prompt}\n\n--- CONTEÚDO COMBINADO (DECRETO + ANEXO III) ---\n{texto_limpo}"

            resposta_objeto = self.model.generate_content(
                conteudo_prompt,
                generation_config=generation_config
            )
            return json.loads(resposta_objeto.text)
        except Exception as e:
            logger.error("[P3] Erro API Gemini (Tentativa %d): %s", tentativa, e)
            time.sleep(2)
            return self._call_gemini(prompt, texto_concatenado, tentativa + 1, max_tentativas)


    def processar_bloco(
        self,
        bloco_tipo_3: str,
        texto_total_ocr: str = "", 
        caminho_pdf_associado: str = ""  
    ) -> Optional[Dict[str, Any]]:
        """
        Processa um bloco Tipo 3.
        """
        logger.info("--- Processando Bloco P3 (Anexo III) ---")

        # 1. Segmentação
        p_corpo, p_anexo, erros = self._segmentar_bloco_decreto(bloco_tipo_3)

        # Filtrar erros críticos para log ou retorno
        erros_criticos = [e for e in erros if "Erro Crítico" in e]
        if erros_criticos:
            logger.warning("[P3] Problemas na segmentação: %s", erros_criticos)

        # 2. Concatenação (Mesclagem)
        # Unimos o corpo (dados gerais) com o Anexo III (fontes)
        texto_mesclado = f"--- CORPO DO DECRETO ---\n{p_corpo}\n\n--- DADOS FINANCEIROS (ANEXO III) ---\n{p_anexo}"

        if not p_anexo and not p_corpo:
            return {"ERRO": "Bloco Vazio", "detalhe": "Segmentação retornou vazio."}

        # 3. Extração AI
        logger.info("Etapa 1: Enviando blocos mesclados para o Gemini...")
        dados_extraidos = self._call_gemini(
            self.PROMPT_ANALISE_Pattern_3, texto_mesclado)

        if not dados_extraidos:
            return {"ERRO": "Falha na API Gemini", "detalhe": "Retorno vazio."}

        # 4. Pós-processamento
        dados_extraidos['valor_total'] = formatar_valor(dados_extraidos.get('valor_total'))
        dados_extraidos['data'] = formatar_data(dados_extraidos.get('data'))

        if 'fontes_detalhadas' in dados_extraidos and isinstance(dados_extraidos['fontes_detalhadas'], list):
            for fonte in dados_extraidos['fontes_detalhadas']:
                fonte['valor'] = formatar_valor(fonte.get('valor'))

        # Flag auxiliar para a camada de apresentação: decreto com Excesso?
        is_excesso = any(
            isinstance(fonte, dict) and fonte.get("fonte") == "Excesso de Arrecadação"
            for fonte in dados_extraidos.get("fontes_detalhadas", [])
        )
        dados_extraidos["_is_excesso"] = is_excesso

        # Anexar alertas
        if erros:
            dados_extraidos['_segmentation_warnings'] = erros

        logger.info("--- Bloco P3 finalizado. ---")
        return dados_extraidos
