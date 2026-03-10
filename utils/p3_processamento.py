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
from datetime import datetime
from typing import List, Optional, Tuple, Dict, Any
import google.generativeai as genai

# ==============================================================================
# CLASSE DE PROCESSAMENTO P3
# ==============================================================================


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
        
        ### INSTRUÇÕES PARA CÓDIGOS ###
        1. Busque o(s) código(s) no corpo do decreto que começa com "CONSIDERANDO".
        2. Caso não encontre no corpo do decreto busque o(s) código(s) no ANEXO III. 
        3. Caso o código tenha mais de 10 dígitos tire 1 ou 2 zeros do final. (ex: "1.604.0000.00.00" vira "1604000000")

        - `"fontes_detalhadas"`: Uma lista de objetos. Crie um objeto apenas para as fontes de recurso do Artigo 2º que possuam um valor monetário (R$) explicitamente associado no texto. Ignore fontes que são apenas mencionadas de forma genérica sem detalhamento financeiro.
            - `"fonte"`: O nome da fonte de recursos, que pode ser "Superávit Financeiro", "Excesso de Arrecadação", "Anulação de Dotações" e/ou "Operações de Crédito". (Normalize "Anulações..." para "Anulação de Dotações").
            - `"valor"`: O valor específico da fonte, encontrado seguindo a HIERARQUIA DE BUSCA acima.
            - `"codigos"`: **CASO a fonte de recurso seja "Excesso de Arrecadação"** extraia os códigos de excesso do corpo do decreto (ex: "CONSIDERANDO ... recursos oriundos do excesso de arrecadação da fonte de Recursos: 1.604.0000.00.00 - Transferências ...")
               
        ### FORMATO DE SAÍDA ###
        {
          "numero": "string", "data": "string", "valor_total": "string", "tipo_credito": "string",
          "fontes_detalhadas": [{"fonte": "string", "valor": "string ou null", "codigos": ["XXXXXXXXXX"]}]
        }
        """

    # --- Métodos Utilitários (Idênticos ao P1/P2) ---

    def _formatar_valor(self, valor_str: str | None) -> str | None:
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

    def _formatar_data(self, data_str: str | None) -> str | None:
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
        # Padrão "dd de Mês de yyyy" (qualquer capitalização)
        match = re.match(r'^(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})\s*$', data_str, re.IGNORECASE)
        if match:
            dia, mes, ano = match.group(1), match.group(2).capitalize(), match.group(3)
            return f"{dia} de {mes} de {ano}"
        return data_str

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
            print("Anexo III' encontrado")
        else:
            # Se não tem Anexo 3, não temos as fontes para esse tipo de decreto
            # erros.append(
            #    "Erro Crítico: 'Anexo III' não encontrado. Dados financeiros podem estar ausentes.")
            print("Anexo III' NAO encontrado")
            # Parte B fica vazia

        return parte_a, parte_b, erros

    # --- Chamada LLM ---

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
            print(f"[P3] Erro API Gemini (Tentativa {tentativa}): {e}")
            time.sleep(2)
            return self._call_gemini(prompt, texto_concatenado, tentativa + 1, max_tentativas)

    # --- Método Orquestrador ---

    def processar_bloco(
        self,
        bloco_tipo_3: str,
        texto_total_ocr: str = "",  # Compatibilidade
        caminho_pdf_associado: str = ""  # Compatibilidade
    ) -> Optional[Dict[str, Any]]:
        """
        Processa um bloco Tipo 3.
        """
        print(f"--- Processando Bloco P3 (Anexo III) ---")

        # 1. Segmentação
        p_corpo, p_anexo, erros = self._segmentar_bloco_decreto(bloco_tipo_3)

        # Filtrar erros críticos para log ou retorno
        erros_criticos = [e for e in erros if "Erro Crítico" in e]
        if erros_criticos:
            print(f"[Aviso P3] Problemas na segmentação: {erros_criticos}")

        # 2. Concatenação (Mesclagem)
        # Unimos o corpo (dados gerais) com o Anexo III (fontes)
        texto_mesclado = f"--- CORPO DO DECRETO ---\n{p_corpo}\n\n--- DADOS FINANCEIROS (ANEXO III) ---\n{p_anexo}"

        if not p_anexo and not p_corpo:
            return {"ERRO": "Bloco Vazio", "detalhe": "Segmentação retornou vazio."}

        # 3. Extração AI
        print("Etapa 1: Enviando blocos mesclados para o Gemini...")
        dados_extraidos = self._call_gemini(
            self.PROMPT_ANALISE_Pattern_3, texto_mesclado)

        if not dados_extraidos:
            return {"ERRO": "Falha na API Gemini", "detalhe": "Retorno vazio."}

        # 4. Pós-processamento
        dados_extraidos['valor_total'] = self._formatar_valor(
            dados_extraidos.get('valor_total'))
        dados_extraidos['data'] = self._formatar_data(
            dados_extraidos.get('data'))

        if 'fontes_detalhadas' in dados_extraidos and isinstance(dados_extraidos['fontes_detalhadas'], list):
            for fonte in dados_extraidos['fontes_detalhadas']:
                fonte['valor'] = self._formatar_valor(fonte.get('valor'))

        # Flag auxiliar para a camada de apresentação: decreto com Excesso?
        is_excesso = any(
            isinstance(fonte, dict) and fonte.get("fonte") == "Excesso de Arrecadação"
            for fonte in dados_extraidos.get("fontes_detalhadas", [])
        )
        dados_extraidos["_is_excesso"] = is_excesso

        # Anexar alertas
        if erros:
            dados_extraidos['_segmentation_warnings'] = erros

        print(f"--- Bloco P3 finalizado. ---\n")
        return dados_extraidos
