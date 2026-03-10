"""
Módulo de Processamento para Decretos "Padrão 2".

Este módulo contém a classe `P2Processor`, especializada em realizar a 
extração de dados de blocos de texto classificados como "Tipo 2".
Diferente do Tipo 1, este padrão foca em marcadores textuais específicos
como "REDU." e a posição relativa dos Artigos 2º e 3º.
"""

import re
import json
import time
import locale
from datetime import datetime
from typing import List, Optional, Tuple, Dict, Any
import google.generativeai as genai

# ==============================================================================
# CLASSE DE PROCESSAMENTO P2
# ==============================================================================


class P2Processor:
    """
    Processador especializado para Decretos Padrão 2.

    Realiza a segmentação do texto baseada em marcadores 'REDU.' e Artigos,
    e utiliza LLM para estruturar os dados financeiros e metadados.
    """

    def __init__(self, gemini_model: genai.GenerativeModel):
        """
        Inicializa o processador com o modelo Gemini e compila os Regexes.
        """
        self.model = gemini_model
        try:
            locale.setlocale(locale.LC_TIME, 'pt_BR.UTF-8')
        except locale.Error:
            # Fallback silencioso ou log se necessário
            pass

        # --- PROMPTS ---
        self.PROMPT_ANALISE_Pattern_2 = """
        ### TAREFA ###
        Sua tarefa é analisar o texto de UM ÚNICO decreto e extrair as informações solicitadas em formato JSON, seguindo TODAS as regras e HIERARQUIAS rigorosamente.

        ### HIERARQUIA DE BUSCA DE VALORES PARA FONTES ###
        Para determinar o `"valor"` de cada fonte de recurso (ex: "Anulação de Dotações", "Excesso de Arrecadação"), siga esta ordem de prioridade:
        1. **Busca (Valor Explícito no Texto):** Procure no texto do Artigo 2º por um valor monetário EXPLICITAMENTE associado ao nome da fonte (ex: "...à conta de Excesso de Arrecadação R$ 3.932.927,00...").
        2.**Busca (Descrição Final):** Busque no trecho antes do Artigo 3º por especificação dos valores.
        3.  **Regra Final:** Se uma fonte for mencionada mas for impossível encontrar um valor explícito, o campo `"valor"` deve ser `null`. (ex: "... e Anulação parcial e/ou total da(s) seguinte(s) dotação(ões) orçamentária(s): ..." mas não houver valore vinculado a anulação desconsidere.) **NÃO FAÇA CÁLCULOS.**

        ### INSTRUÇÕES DE EXTRAÇÃO ###
        - `"numero"`: O número do decreto, extraído do título.
        - `"data"`: A data do decreto, extraída do título.
        - `"valor_total"`: O valor total do Artigo 1º.
        - `"tipo_credito"`: O tipo de crédito adicional do Artigo 1º que pode ser "Suplementar", "Especial" ou "Extraordinário". (Normalize para conter "Créditos" e o tipo: exemplo: "Créditos Suplementares")

        - `"fontes_detalhadas"`: Uma lista de objetos. Crie um objeto apenas para as fontes de recurso do Artigo 2º que possuam um valor monetário (R$) explicitamente associado no texto. Ignore fontes que são apenas mencionadas de forma genérica sem detalhamento financeiro.
            - `"fonte"`: O nome da fonte de recursos, que pode ser "Superávit Financeiro", "Excesso de Arrecadação", "Anulação de Dotações" e/ou "Operações de Crédito". (Normalize "Anulações..." para "Anulação de Dotações").
            - `"valor"`: O valor específico da fonte, encontrado seguindo a HIERARQUIA DE BUSCA acima.
            - `"codigos"`: Deixe vazio por enquanto.
            
        ### FORMATO DE SAÍDA ###
        {
          "numero": "string", "data": "string", "valor_total": "string", "tipo_credito": "string",
          "fontes_detalhadas": [{"fonte": "string", "valor": "string ou null", "codigos": ["XXXXXXXXXX"]}]
        }
        """

        # --- REGEX PATTERNS (Compilados para performance) ---

        # Regex para o Artigo 3° (Flexível para º ou °)
        self.REGEX_ART_3 = re.compile(r'Art\.\s*3[º°]\.\s*', re.IGNORECASE)

        # Regex estrito para o termo REDU.
        self.REGEX_REDU = re.compile(r'REDU\.', re.IGNORECASE)

        # Regex estrito para o Artigo 2°
        self.REGEX_ART_2 = re.compile(r'Art\.\s*2[º°]', re.IGNORECASE)

    # --- Métodos Utilitários de Formatação (Reutilizados do P1) ---

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

    # --- Lógica de Segmentação Core (Baseada na P2_Logica) ---

    def _segmentar_bloco_decreto(self, bloco: str) -> Tuple[str, str, str, List[str]]:
        """
        Implementa a lógica de particionamento em 3 etapas para Decretos Tipo 2.
        Retorna: (parte_1, parte_2, parte_3, lista_de_erros)
        """
        erros = []

        # Encontra todas as ocorrências chave
        match_art_3 = self.REGEX_ART_3.search(bloco)
        art_2_matches = list(self.REGEX_ART_2.finditer(bloco))
        redu_matches = list(self.REGEX_REDU.finditer(bloco))

        # ---------------------------------------------------------
        # 1. CAPTURA DA PARTE 1 (Início -> REDU ou Art 3)
        # ---------------------------------------------------------
        parte_1 = ""
        pos_fim_p1 = 0

        if redu_matches:
            # Caso Ideal: Vai até o primeiro REDU.
            pos_fim_p1 = redu_matches[0].end()
            parte_1 = bloco[:pos_fim_p1]
        elif match_art_3:
            # Fallback: Se não tem REDU, vai até Art 3.
            pos_fim_p1 = match_art_3.start()
            parte_1 = bloco[:pos_fim_p1]
            # erros.append(
            #    "Aviso: 'REDU.' não encontrado para Parte 1. Usando corte no Art. 3º.")
            print("Aviso: 'REDU.' não encontrado para Parte 1. Usando corte no Art. 3º.")
        else:
            # Estrutura Inválida Crítica
            erros.append(
                "Erro Crítico: Nem 'REDU.' nem 'Art. 3º' encontrados para definir Parte 1.")
            return "", "", "", erros

        # ---------------------------------------------------------
        # 2. CAPTURA DA PARTE 2 (Art 2 -> REDU ou Art 3)
        # ---------------------------------------------------------
        parte_2 = ""

        if not art_2_matches:
            erros.append("Erro Crítico: 'Art. 2º' não encontrado.")
            return "", "", "", erros

        # Lógica: O Art 2º real costuma ser o ÚLTIMO encontrado (devido a repetições de cabeçalho)
        # Mas verificamos se ele está logicamente posicionado antes do Art 3
        pos_art_2_inicio = art_2_matches[-1].start()
        if match_art_3 and pos_art_2_inicio > match_art_3.start():
            # Se o último Art 2 está depois do Art 3, algo está errado, tenta o primeiro ou anterior
            # Fallback simples: pega o primeiro Art 2 se o último falhar na lógica
            pos_art_2_inicio = art_2_matches[0].start()

        # Busca REDU após o Art 2
        redu_depois_art2 = [
            m for m in redu_matches if m.start() > pos_art_2_inicio]

        if redu_depois_art2:
            # Caso Ideal: Vai do Art 2 até o primeiro REDU subsequente
            pos_redu_2_fim = redu_depois_art2[0].end()
            parte_2 = bloco[pos_art_2_inicio:pos_redu_2_fim]
        elif match_art_3:
            # Fallback: Se não tem REDU após Art 2, vai até Art 3
            parte_2 = bloco[pos_art_2_inicio:match_art_3.start()]
            # erros.append(
            #    "Aviso: 'REDU.' pós-Art. 2º não encontrado. Usando corte no Art. 3º.")
            print("Aviso: 'REDU.' pós-Art. 2º não encontrado. Usando corte no Art. 3º.")
        else:
            # Se não tem REDU nem Art 3 para fechar o Art 2
            erros.append(
                "Erro Crítico: Impossível delimitar o fim da Parte 2 (Art. 2º).")
            return "", "", "", erros

        # ---------------------------------------------------------
        # 3. CAPTURA DA PARTE 3 (Art 3 Retrocendendo -> Fim)
        # ---------------------------------------------------------
        parte_3 = ""

        if match_art_3:
            # Logica: Captura do artigo 3 para cima até a ultima ocorrencia de REDU
            pos_art_3_inicio = match_art_3.start()

            # Procura a última REDU que vem ANTES do Art 3
            redus_antes_art3 = [
                m for m in redu_matches if m.end() <= pos_art_3_inicio]

            if redus_antes_art3:
                # Corta a partir dessa última REDU até o fim do bloco
                # (Ou seja, pega o finalzinho das reduções e o fechamento)
                pos_inicio_p3 = redus_antes_art3[-1].start()
                parte_3 = bloco[pos_inicio_p3:]
            else:
                # Fallback: Captura 500 caracteres antes do Art 3
                retrocesso = 500
                pos_inicio_p3 = max(0, pos_art_3_inicio - retrocesso)
                parte_3 = bloco[pos_inicio_p3:]
                # erros.append(
                #    "Aviso: Nenhuma 'REDU.' antes do Art. 3º para corte preciso. Usando retrocesso de 500 chars.")
                print(
                    "Aviso: Nenhuma 'REDU.' antes do Art. 3º para corte preciso. Usando retrocesso de 500 chars.")
        else:
            erros.append(
                "Erro Crítico: 'Art. 3º' não encontrado para Parte 3.")
            return "", "", "", erros

        return parte_1.strip(), parte_2.strip(), parte_3.strip(), erros

    # --- Integração com Gemini ---

    def _call_gemini(self, prompt: str, texto_concatenado: str, tentativa=1, max_tentativas=3) -> dict | None:
        if tentativa > max_tentativas:
            return None
        try:
            generation_config = genai.GenerationConfig(
                response_mime_type="application/json")

            # Limpeza básica
            texto_limpo = self._limpar_texto(texto_concatenado)
            conteudo_prompt = f"{prompt}\n\n--- TEXTO DO DECRETO (SEGMENTADO E UNIDO) ---\n{texto_limpo}"

            resposta_objeto = self.model.generate_content(
                conteudo_prompt,
                generation_config=generation_config
            )

            return json.loads(resposta_objeto.text)

        except Exception as e:
            print(f"[P2] Erro API Gemini (Tentativa {tentativa}): {e}")
            time.sleep(2)
            return self._call_gemini(prompt, texto_concatenado, tentativa + 1, max_tentativas)

    # --- Método Público Principal ---

    def processar_bloco(
        self,
        bloco_tipo_2: str,
        texto_total_ocr: str = "",  # Mantido para compatibilidade de assinatura
        caminho_pdf_associado: str = ""  # Mantido para compatibilidade de assinatura
    ) -> Optional[Dict[str, Any]]:
        """
        Processa um bloco de texto classificado como Tipo 2.

        Args:
            bloco_tipo_2: O texto bruto do decreto.
            texto_total_ocr: (Opcional para P2) Texto completo do arquivo.
            caminho_pdf_associado: (Opcional para P2) Caminho do PDF.

        Returns:
            Dict com os dados extraídos ou Dicionário de Erro.
        """
        print(f"--- Processando Bloco P2 (Lógica REDU/Artigos) ---")

        # 1. Segmentação
        p1, p2, p3, erros = self._segmentar_bloco_decreto(bloco_tipo_2)

        # Verificação de Erros Críticos (aqueles que retornaram strings vazias nas partes vitais)
        erros_criticos = [e for e in erros if "Erro Crítico" in e]
        if erros_criticos:
            print(f"[Erro P2] Falha na segmentação: {erros_criticos[0]}")
            return {"ERRO": "Falha Estrutural P2", "detalhe": erros_criticos}

        if erros:
            print(f"[Aviso P2] Alertas de segmentação: {erros}")

        # 2. Preparação para LLM
        # Concatenamos as partes relevantes. Isso remove o "lixo" entre as seções.
        texto_para_analise = f"--- INICIO ---\n{p1}\n\n--- MEIO (FONTES) ---\n{p2}\n\n--- FIM (FECHAMENTO) ---\n{p3}"

        print("Etapa 1: Enviando partes segmentadas para o Gemini...")

        # 3. Chamada LLM
        dados_extraidos = self._call_gemini(
            self.PROMPT_ANALISE_Pattern_2, texto_para_analise)

        if not dados_extraidos:
            return {"ERRO": "Falha na API Gemini", "detalhe": "Retorno vazio ou inválido."}

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

        # Adiciona metadados de debug se houver avisos
        if erros:
            dados_extraidos['_segmentation_warnings'] = erros

        print(f"--- Bloco P2 finalizado com sucesso. ---\n")
        return dados_extraidos
