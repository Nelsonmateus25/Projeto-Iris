import os
import base64
import uuid
import tempfile
import logging
import traceback
import pandas as pd
import google.generativeai as genai
import fitz  # PyMuPDF
import streamlit as st
from io import BytesIO
from dotenv import load_dotenv

from utils.processamento_txt import (
    extrair_blocos_decreto_com_paginas,
    identificar_padrao_global,
)
from utils.p1_processamento import P1Processor
from utils.p2_processamento import P2Processor
from utils.p3_processamento import P3Processor
from utils.pdf_to_txt import (
    upload_to_s3,
    extract_text_textract_s3,
    delete_from_s3,
    BUCKET_NAME,
)

#load_dotenv(override=True)
load_dotenv()

# Configura o logging uma única vez, no ponto de entrada da aplicação.
# Todos os módulos (p1, p2, p3, pdf_to_txt) herdam essa configuração
# e seus logs aparecerão no terminal com timestamp e nome do módulo.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# INICIALIZAÇÃO DO MODELO 
def carregar_processadores():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY não encontrada. Verifique seu .env.")
    genai.configure(api_key=api_key)
    model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    model = genai.GenerativeModel(model_name)
    p1 = P1Processor(model)
    p2 = P2Processor(model)
    p3 = P3Processor(model)
    return p1, p2, p3



# FUNÇÕES DE GERAÇÃO DE RELATÓRIO 
def gerar_quadros_html(dados_extraidos: list, erros: list) -> str:
    """
    Gera o HTML dos quadros de SUCESSO e de ERRO/ALERTA.
    A coluna 'Anexos (Excesso)' é omitida pois os anexos são exibidos
    via st.expander diretamente na interface Streamlit.
    """

    def criar_linha_html(items):
        safe_items = [str(item) if item is not None else '' for item in items]
        return "<tr>" + "".join(f"<td>{item}</td>" for item in safe_items) + "</tr>"

    html = ""

    # --- Seção de Erros e Alertas ---
    html_q3_erros = "<h3>Erros e Alertas de Verificação</h3>"
    alertas_encontrados = []
    for dado in dados_extraidos:
        if "_verification_alerts" in dado:
            decreto_num = dado.get("numero", "N/A")
            for alerta_detalhe in dado["_verification_alerts"]:
                alertas_encontrados.append({
                    "numero": decreto_num,
                    "detalhe": alerta_detalhe
                })
        if "_segmentation_warnings" in dado:
            decreto_num = dado.get("numero", "N/A")
            for alerta_detalhe in dado["_segmentation_warnings"]:
                alertas_encontrados.append({
                    "numero": decreto_num,
                    "detalhe": f"[Aviso de Segmentação] {alerta_detalhe}"
                })

    tem_erros_ou_alertas = bool(erros) or bool(alertas_encontrados)
    classe_erro = "error-summary" if tem_erros_ou_alertas else "error-summary no-errors"
    html_q3_erros += f"<div class='{classe_erro}'>"

    if not erros and not alertas_encontrados:
        html_q3_erros += "<p>Nenhum erro fatal ou alerta de verificação foi encontrado.</p>"

    if erros:
        total_blocos = len(dados_extraidos) + len(erros)
        html_q3_erros += f"<p><strong>{len(erros)} de {total_blocos} blocos falharam (Erros Fatais).</strong></p>"
        html_q3_erros += "<table><thead><tr><th>Bloco Nº</th><th>Tipo</th><th>Motivo da Falha</th><th>Detalhe</th></tr></thead><tbody>"
        for erro in erros:
            detalhe = str(erro.get('detalhe', 'N/A')).replace('<', '&lt;').replace('>', '&gt;')
            linha_erro = [
                erro.get('bloco_num', '?'),
                erro.get('tipo', 'N/A'),
                erro.get('motivo', 'Erro'),
                detalhe
            ]
            html_q3_erros += criar_linha_html(linha_erro)
        html_q3_erros += "</tbody></table>"

    if alertas_encontrados:
        if erros:
            html_q3_erros += "<hr style='margin: 20px 0; border-top: 1px solid #f3caca;'>"
        html_q3_erros += f"<p><strong>{len(alertas_encontrados)} Alertas de Verificação encontrados em blocos processados com sucesso.</strong></p>"
        html_q3_erros += "<table><thead><tr><th>Decreto Nº</th><th>Tipo de Alerta</th><th>Detalhe</th></tr></thead><tbody>"
        for alerta in alertas_encontrados:
            detalhe_alerta = str(alerta.get('detalhe', 'N/A')).replace('<', '&lt;').replace('>', '&gt;')
            linha_alerta = [
                alerta.get('numero', '?'),
                "Aviso de Processamento",
                detalhe_alerta
            ]
            html_q3_erros += criar_linha_html(linha_alerta)
        html_q3_erros += "</tbody></table>"

    html_q3_erros += "</div>"
    html += f"{html_q3_erros}<br>"

    # --- Quadro 1: Decretos Identificados (Sucessos) ---
    html_q1 = f"<h4>Quadro 1: {len(dados_extraidos)} Decretos Identificados com Sucesso</h4>"
    html_q1 += "<table><thead><tr><th>Número</th><th>Data</th><th>Valor Total (R$)</th><th>Tipo de Crédito</th><th>Fontes de Recurso</th></tr></thead><tbody>"
    if not dados_extraidos:
        html_q1 += "<tr><td colspan='5'>Nenhum decreto foi extraído com sucesso.</td></tr>"
    else:
        for dado in dados_extraidos:
            fontes_detalhadas = dado.get("fontes_detalhadas", [])
            if not isinstance(fontes_detalhadas, list):
                fontes_detalhadas = []
            fontes_lista = [d.get("fonte") for d in fontes_detalhadas if d.get("fonte")]
            fontes_agregadas = ", ".join(fontes_lista) if fontes_lista else "N/A"
            linha = [
                dado.get("numero"),
                dado.get("data"),
                dado.get("valor_total"),
                dado.get("tipo_credito"),
                fontes_agregadas,
            ]
            html_q1 += criar_linha_html(linha)
    html_q1 += "</tbody></table>"
    html += html_q1

    # --- Quadro 2: Detalhamento por Fonte (Sucessos) ---
    html_q2 = "<h4>Quadro 2: Detalhamento por Fonte de Recurso (Sucessos)</h4>"
    html_q2 += "<table><thead><tr><th>Número</th><th>Data</th><th>Valor da Fonte (R$)</th><th>Fonte de Recurso</th></tr></thead><tbody>"
    linhas_q2_geradas = 0
    for dado in dados_extraidos:
        fontes_detalhadas = dado.get("fontes_detalhadas", [])
        if not isinstance(fontes_detalhadas, list):
            fontes_detalhadas = []
        fontes_com_valor = [d for d in fontes_detalhadas if d.get("valor") is not None]
        if len(fontes_com_valor) > 1:
            for detalhe in fontes_com_valor:
                linha = [dado.get("numero"), dado.get("data"), detalhe.get("valor", "N/A"), detalhe.get("fonte")]
                html_q2 += criar_linha_html(linha)
                linhas_q2_geradas += 1

    if linhas_q2_geradas == 0:
        html_q2 += "<tr><td colspan='4'>Nenhum dado detalhado de fontes encontrado.</td></tr>"
    html_q2 += "</tbody></table>"
    html += f"<br>{html_q2}"

    return html


def gerar_quadros_dataframe(dados_extraidos: list) -> tuple:
    """Gera os DataFrames para os botões de download (Quadros 1 e 2)."""
    linhas_q1, linhas_q2 = [], []

    for dado in dados_extraidos:
        numero = dado.get("numero", "N/A")
        data = dado.get("data", "N/A")
        valor_total = dado.get("valor_total", "N/A")
        tipo_credito = dado.get("tipo_credito", "N/A")
        fontes_detalhadas = dado.get("fontes_detalhadas", [])
        if not isinstance(fontes_detalhadas, list):
            fontes_detalhadas = []

        fontes_lista = [d.get("fonte") for d in fontes_detalhadas if d.get("fonte")]
        fontes_agregadas = ", ".join(fontes_lista) if fontes_lista else "N/A"
        linhas_q1.append({
            "Número do Decreto": numero,
            "Data do Decreto": data,
            "Valor do Decreto (R$)": valor_total,
            "Tipo de Crédito Adicional": tipo_credito,
            "Fonte de Recurso do Crédito Adicional": fontes_agregadas,
        })

        fontes_com_valor = [d for d in fontes_detalhadas if d.get("valor") is not None]
        if len(fontes_com_valor) > 1:
            for detalhe in fontes_com_valor:
                linhas_q2.append({
                    "Número do Decreto": numero,
                    "Data do Decreto": data,
                    "Valor da Fonte de Recurso (R$)": detalhe.get("valor", "N/A"),
                    "Fonte de Recurso do Crédito Adicional": detalhe.get("fonte", "N/A"),
                })

    return pd.DataFrame(linhas_q1), pd.DataFrame(linhas_q2)

# FUNÇÕES AUXILIARES 
def extrair_imagem_pagina(pdf_path: str, page_num: int) -> bytes | None:
    """Retorna os bytes JPEG de uma página do PDF (1-based)."""
    try:
        doc = fitz.open(pdf_path)
        index = page_num - 1
        if index < 0 or index >= len(doc):
            doc.close()
            return None
        page = doc.load_page(index)
        pix = page.get_pixmap(matrix=fitz.Matrix(150 / 72, 150 / 72))
        img_bytes = pix.tobytes("jpeg")
        doc.close()
        return img_bytes
    except Exception as e:
        st.error(f"Erro ao gerar imagem da página {page_num}: {e}")
        return None


def gerar_pdf_paginas(pdf_path: str, paginas: list) -> bytes | None:
    """Gera um novo PDF contendo apenas as páginas especificadas (1-based)."""
    try:
        doc_orig = fitz.open(pdf_path)
        doc_novo = fitz.open()
        for page_num in paginas:
            idx_page = page_num - 1
            if 0 <= idx_page < len(doc_orig):
                doc_novo.insert_pdf(doc_orig, from_page=idx_page, to_page=idx_page)
        doc_orig.close()
        pdf_bytes = doc_novo.write()
        doc_novo.close()
        return pdf_bytes
    except Exception as e:
        st.error(f"Erro ao gerar PDF das páginas: {e}")
        return None


# ==============================================================================
# PROCESSAMENTO PRINCIPAL
# ==============================================================================

def executar_processamento(pdf_path: str, texto_total: str):
    """
    Extrai blocos, identifica padrão, processa com P1/P2/P3 e
    armazena resultados no session_state.
    """
    p1_processor, p2_processor, p3_processor = carregar_processadores()

    blocos_com_paginas = extrair_blocos_decreto_com_paginas(texto_total)
    tipo_global = identificar_padrao_global(texto_total)

    if tipo_global == "P3":
        processador_ativo = p3_processor
    elif tipo_global == "P2":
        processador_ativo = p2_processor
    else:
        processador_ativo = p1_processor

    resultados = []
    erros = []
    total = len(blocos_com_paginas)

    progress_bar = st.progress(0, text="Iniciando processamento...")

    for i, (bloco, paginas_bloco) in enumerate(blocos_com_paginas):
        progress_bar.progress(
            (i + 1) / total if total > 0 else 1,
            text=f"Processando bloco {i + 1} de {total} (padrão {tipo_global})..."
        )

        dados = processador_ativo.processar_bloco(bloco, texto_total, pdf_path)

        if dados and "ERRO" not in dados:
            dados["_pdf_pages"] = paginas_bloco
            dados["_bloco_index"] = i
            resultados.append(dados)
        else:
            motivo = dados.get("ERRO") if dados else "Retorno Vazio"
            detalhe = dados.get("detalhe") if dados else "N/A"
            erros.append({
                "bloco_num": i + 1,
                "tipo": tipo_global,
                "motivo": motivo,
                "detalhe": detalhe,
            })

    progress_bar.empty()

    st.session_state["dados_extraidos"] = resultados
    st.session_state["decretos_meta"] = {
        "pdf_path": pdf_path,
        "tipo_global": tipo_global,
        "blocos": [
            {
                "index": idx,
                "numero": dado.get("numero"),
                "is_excesso": bool(dado.get("_is_excesso")),
                "pages": dado.get("_pdf_pages", []),
            }
            for idx, dado in enumerate(resultados)
        ],
    }
    st.session_state["erros_processamento"] = erros



def carregar_css() -> None:
    """Lê assets/style.css e injeta como bloco <style> na página."""
    css_path = os.path.join(os.path.dirname(__file__), "assets", "style.css")
    with open(css_path, encoding="utf-8") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)


# ==============================================================================
# INTERFACE STREAMLIT
# ==============================================================================

st.set_page_config(
    page_title="Analisador de Decretos",
    page_icon="📄",
    layout="wide",
)

carregar_css()
st.title("Analisador de Decretos")

# --- Seção 1: Upload ---
st.header("1. Enviar Arquivos")
st.write("Envie o PDF do decreto")

col1, col2 = st.columns(2)
with col1:
    st.caption("Arquivo PDF Original (.pdf) — obrigatório")
    pdf_upload = st.file_uploader("PDF obrigatório", type=["pdf"], label_visibility="collapsed")
with col2:
    st.caption("Arquivo TXT (opcional — gerado automaticamente via Textract se não enviado)")
    txt_upload = st.file_uploader("TXT opcional", type=["txt"], label_visibility="collapsed")

analisar = st.button("Analisar", type="primary", disabled=not pdf_upload)

if analisar and pdf_upload:
    # Apaga o arquivo temporário da análise ANTERIOR antes de criar um novo.
    # Estratégia: não apagamos o arquivo ao terminar (a galeria precisa dele),
    # mas apagamos no início da próxima análise, quando ele deixa de ser necessário.
    pdf_path_anterior = st.session_state.get("decretos_meta", {}).get("pdf_path", "")
    if pdf_path_anterior and os.path.exists(pdf_path_anterior):
        try:
            os.unlink(pdf_path_anterior)
            logger.info("Arquivo temporário anterior removido: %s", pdf_path_anterior)
        except OSError:
            logger.warning("Não foi possível remover arquivo temporário anterior: %s", pdf_path_anterior)

    # UUID garante nome único mesmo se dois usuários enviarem o mesmo arquivo ao mesmo tempo.
    # delete=False mantém o arquivo no disco após o close() — necessário pois outros
    # módulos (PyMuPDF, processadores) precisam acessar o arquivo pelo caminho depois.
    uid = uuid.uuid4().hex
    tmp_pdf = tempfile.NamedTemporaryFile(
        suffix=f"_{uid}_{pdf_upload.name}",
        delete=False,
    )
    tmp_pdf.write(pdf_upload.getvalue())
    tmp_pdf.flush()
    pdf_path = tmp_pdf.name
    tmp_pdf.close()

    texto_total = None

    # --- Caminho 1: TXT fornecido → usa diretamente ---
    if txt_upload:
        try:
            texto_total = txt_upload.getvalue().decode("utf-8")
        except UnicodeDecodeError:
            texto_total = txt_upload.getvalue().decode("latin-1")
        st.info("TXT fornecido — usando diretamente como texto OCR. PDF será usado apenas para extração de imagens.")

    # --- Caminho 2: Só PDF → gera texto via Amazon Textract ---
    else:
        st.info("Enviando PDF para o Amazon Textract. Isso pode levar alguns minutos...")
        # UUID também na chave S3 evita colisões de nomes no bucket
        s3_key = f"textract-uploads/{uid}_{pdf_upload.name}"
        with st.spinner("Fazendo upload do PDF para o S3..."):
            upload_ok = upload_to_s3(pdf_path, BUCKET_NAME, s3_key)

        if not upload_ok:
            st.error("Falha no upload para o S3. Verifique as credenciais AWS no arquivo .env.")
        else:
            with st.spinner("Extraindo texto com Amazon Textract (pode demorar alguns minutos)..."):
                texto_total = extract_text_textract_s3(BUCKET_NAME, s3_key)
                delete_from_s3(BUCKET_NAME, s3_key)

            if not texto_total or "[Erro Textract S3]" in texto_total:
                st.error("Falha na extração do Textract. Verifique as permissões do bucket S3 e tente novamente.")
                texto_total = None

    if texto_total:
        with st.spinner("Analisando decretos... Isso pode levar alguns minutos."):
            try:
                executar_processamento(pdf_path, texto_total)
            except Exception as e:
                logger.exception("Erro não tratado durante executar_processamento: %s", e)
                st.error(
                    "Ocorreu um erro inesperado durante o processamento. "
                    "Verifique o arquivo PDF e tente novamente. "
                    "Se o problema persistir, contate o administrador."
                )
    else:
        # Se não houve texto para processar, o arquivo temporário não será mais
        # necessário (não há galeria para exibir), então pode ser removido agora.
        try:
            os.unlink(pdf_path)
            logger.info("Arquivo temporário removido (sem processamento): %s", pdf_path)
        except OSError:
            logger.warning("Não foi possível remover arquivo temporário: %s", pdf_path)

# --- Seção 2: Resultados ---
st.header("2. Resultados da Análise")

if "dados_extraidos" not in st.session_state:
    st.info("Envie um PDF para iniciar a análise.")
else:
    resultados = st.session_state["dados_extraidos"]
    erros = st.session_state.get("erros_processamento", [])

    html_quadros = gerar_quadros_html(resultados, erros)
    st.markdown(html_quadros, unsafe_allow_html=True)

    # --- Botões de Download Excel ---
    if resultados:
        st.markdown("---")
        df_q1, df_q2 = gerar_quadros_dataframe(resultados)

        col_dl1, col_dl2 = st.columns(2)

        with col_dl1:
            buf_q1 = BytesIO()
            df_q1.to_excel(buf_q1, index=False)
            st.download_button(
                label="⬇️ Baixar Quadro 1 (Excel)",
                data=buf_q1.getvalue(),
                file_name="quadro_1.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

        with col_dl2:
            buf_q2 = BytesIO()
            df_q2.to_excel(buf_q2, index=False)
            st.download_button(
                label="⬇️ Baixar Quadro 2 (Excel)",
                data=buf_q2.getvalue(),
                file_name="quadro_2.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

    # --- Galeria de Anexos (Excesso de Arrecadação) ---
    decretos_excesso = [d for d in resultados if d.get("_is_excesso")]
    if decretos_excesso:
        st.markdown("---")
        st.header("3. Anexos — Decretos de Excesso de Arrecadação")
        meta = st.session_state.get("decretos_meta", {})
        pdf_path_salvo = meta.get("pdf_path", "")

        for dado in decretos_excesso:
            numero = dado.get("numero", "Decreto")
            paginas = dado.get("_pdf_pages", [])
            label_expander = f"Ver anexos — Decreto {numero} ({len(paginas)} página(s))"

            with st.expander(label_expander):
                if not pdf_path_salvo or not os.path.exists(pdf_path_salvo):
                    st.warning("Arquivo PDF não disponível para visualização.")
                elif not paginas:
                    st.warning("Nenhuma página associada a este decreto.")
                else:
                    # Botão para baixar PDF das páginas do decreto
                    pdf_paginas = gerar_pdf_paginas(pdf_path_salvo, paginas)
                    if pdf_paginas:
                        nome_pdf = f"anexos_decreto_{numero}.pdf".replace("/", "-").replace(" ", "_")
                        st.download_button(
                            label="⬇️ Baixar PDF das páginas",
                            data=pdf_paginas,
                            file_name=nome_pdf,
                            mime="application/pdf",
                            key=f"dl_pdf_{numero}",
                        )

                    # Galeria de imagens — ícone quadrado clicável, páginas abrem verticalmente
                    imagens_html_parts = []
                    for page_num in paginas:
                        img_bytes = extrair_imagem_pagina(pdf_path_salvo, page_num)
                        if img_bytes:
                            b64 = base64.b64encode(img_bytes).decode()
                            imagens_html_parts.append(
                                f"<div class='pg-item'>"
                                f"<h4>Página {page_num}</h4>"
                                f"<img src='data:image/jpeg;base64,{b64}' alt='Página {page_num}' />"
                                f"</div>"
                            )
                        else:
                            imagens_html_parts.append(
                                f"<div class='pg-item'>"
                                f"<p style='color:#a94442;'>Página {page_num}: não foi possível carregar.</p>"
                                f"</div>"
                            )

                    if imagens_html_parts:
                        galeria_html = (
                            "<details class='galeria-decreto'>"
                            "<summary title='Clique para ver as páginas'>PDF</summary>"
                            "<div class='galeria-scroll'>"
                            + "".join(imagens_html_parts)
                            + "</div></details>"
                        )
                        st.markdown(galeria_html, unsafe_allow_html=True)
