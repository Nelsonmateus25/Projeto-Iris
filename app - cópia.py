import os
import markdown
import pandas as pd
import google.generativeai as genai
from io import BytesIO
from dotenv import load_dotenv
from flask import Flask, render_template, request, send_file, jsonify

# --- 1. Imports dos Módulos de Utils ---
from utils.processamento_txt import (
    extrair_blocos_decreto,
    extrair_blocos_decreto_com_paginas,
    classificar_blocos,
    identificar_padrao_global,
)
from utils.p1_processamento import P1Processor
from utils.p2_processamento import P2Processor
from utils.p3_processamento import P3Processor


load_dotenv(override=True)
app = Flask(__name__)


app.config['DADOS_EXTRAIDOS'] = []
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['GEMINI_API_KEY'] = os.getenv("GEMINI_API_KEY")

if not app.config['GEMINI_API_KEY']:
    raise ValueError("GEMINI_API_KEY não encontrada. Verifique seu .env.")

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# --- 3. Inicialização dos Modelos
try:
    genai.configure(api_key=app.config['GEMINI_API_KEY'])
    gemini_model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    gemini_model = genai.GenerativeModel(gemini_model_name)

    p1_processor = P1Processor(gemini_model)
    p2_processor = P2Processor(gemini_model)
    # [MODIFICAÇÃO 1] Inicializar o P3Processor
    p3_processor = P3Processor(gemini_model)

    print(
        f"Modelo Gemini ({gemini_model_name}) e processadores (P1, P2, P3) inicializados.")

except Exception as e:
    print(f"Erro fatal ao inicializar o modelo Gemini: {e}")
    raise

# ==============================================================================
# FUNÇÕES DE GERAÇÃO DE RELATÓRIO
# ==============================================================================


def gerar_quadros_html(dados_extraidos: list, erros: list) -> str:
    """
    Gera o HTML dos quadros de SUCESSO e de ERRO/ALERTA.
    """

    def criar_linha_html(items):
        safe_items = [str(item) if item is not None else '' for item in items]
        return "<tr>" + "".join(f"<td>{item}</td>" for item in safe_items) + "</tr>"

    html = ""

    # --- Seção de Erros e Alertas (no topo, não mais como 'Quadro 3') ---
    html_q3_erros = "<h3>Erros e Alertas de Verificação</h3>"
    alertas_encontrados = []
    for dado in dados_extraidos:
        # Alertas do P1
        if "_verification_alerts" in dado:
            decreto_num = dado.get("numero", "N/A")
            for alerta_detalhe in dado["_verification_alerts"]:
                alertas_encontrados.append({
                    "numero": decreto_num,
                    "detalhe": alerta_detalhe
                })
        # Alertas do P2 e P3 (Ambos usam a chave warning)
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

    # Bloco de Erros Fatais
    if erros:
        total_blocos = len(dados_extraidos) + len(erros)
        html_q3_erros += f"<p><strong>{len(erros)} de {total_blocos} blocos falharam (Erros Fatais).</strong></p>"
        html_q3_erros += "<table><thead><tr><th>Bloco Nº</th><th>Tipo</th><th>Motivo da Falha</th><th>Detalhe</th></tr></thead><tbody>"
        for erro in erros:
            detalhe = str(erro.get('detalhe', 'N/A')).replace('<',
                                                              '&lt;').replace('>', '&gt;')
            linha_erro = [
                erro.get('bloco_num', '?'),
                erro.get('tipo', 'N/A'),
                erro.get('motivo', 'Erro'),
                detalhe
            ]
            html_q3_erros += criar_linha_html(linha_erro)
        html_q3_erros += "</tbody></table>"

    # Bloco de Alertas de Verificação
    if alertas_encontrados:
        if erros:
            html_q3_erros += "<hr style='margin: 20px 0; border-top: 1px solid #f3caca;'>"

        html_q3_erros += f"<p><strong>{len(alertas_encontrados)} Alertas de Verificação encontrados em blocos processados com sucesso.</strong></p>"
        html_q3_erros += "<table><thead><tr><th>Decreto Nº</th><th>Tipo de Alerta</th><th>Detalhe</th></tr></thead><tbody>"
        for alerta in alertas_encontrados:
            detalhe_alerta = str(alerta.get('detalhe', 'N/A')
                                 ).replace('<', '&lt;').replace('>', '&gt;')
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
    html_q1 += "<table><thead><tr><th>Número</th><th>Data</th><th>Valor Total (R$)</th><th>Tipo de Crédito</th><th>Fontes de Recurso</th><th>Anexos (Excesso)</th></tr></thead><tbody>"
    if not dados_extraidos:
        html_q1 += "<tr><td colspan='6'>Nenhum decreto foi extraído com sucesso.</td></tr>"
    else:
        for idx, dado in enumerate(dados_extraidos):
            fontes_detalhadas = dado.get("fontes_detalhadas", [])
            if not isinstance(fontes_detalhadas, list):
                fontes_detalhadas = []
            fontes_lista = [d.get("fonte")
                            for d in fontes_detalhadas if d.get("fonte")]
            fontes_agregadas = ", ".join(
                fontes_lista) if fontes_lista else "N/A"

            is_excesso = bool(dado.get("_is_excesso"))
            if is_excesso:
                anexos_html = f"<a href='/decreto_imagens/{idx}' target='_blank'>Ver anexos</a>"
            else:
                anexos_html = ""

            linha = [dado.get("numero"), dado.get("data"), dado.get(
                "valor_total"), dado.get("tipo_credito"), fontes_agregadas, anexos_html]
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

        fontes_com_valor = [
            d for d in fontes_detalhadas if d.get("valor") is not None]
        if len(fontes_com_valor) > 1:
            for detalhe in fontes_com_valor:
                linha = [dado.get("numero"), dado.get("data"), detalhe.get(
                    "valor", "N/A"), detalhe.get("fonte")]
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

        # Lógica Q1
        fontes_lista = [d.get("fonte")
                        for d in fontes_detalhadas if d.get("fonte")]
        fontes_agregadas = ", ".join(fontes_lista) if fontes_lista else "N/A"
        linhas_q1.append({
            "Número do Decreto": numero, "Data do Decreto": data,
            "Valor do Decreto (R$)": valor_total, "Tipo de Crédito Adicional": tipo_credito,
            "Fonte de Recurso do Crédito Adicional": fontes_agregadas
        })

        # Lógica Q2
        fontes_com_valor = [
            d for d in fontes_detalhadas if d.get("valor") is not None]
        if len(fontes_com_valor) > 1:
            for detalhe in fontes_com_valor:
                linhas_q2.append({
                    "Número do Decreto": numero,
                    "Data do Decreto": data,
                    "Valor da Fonte de Recurso (R$)": detalhe.get("valor", "N/A"),
                    "Fonte de Recurso do Crédito Adicional": detalhe.get("fonte", "N/A")
                })

    return pd.DataFrame(linhas_q1), pd.DataFrame(linhas_q2)


# ==============================================================================
# ROTAS FLASK
# ==============================================================================

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/upload', methods=['POST'])
def upload():
    pdf_file = request.files.get('pdf_file')
    txt_file = request.files.get('txt_file')

    if not pdf_file or not txt_file:
        return "Arquivos inválidos", 400

    try:
        # 1. Salvar arquivos
        pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], pdf_file.filename)
        txt_path = os.path.join(app.config['UPLOAD_FOLDER'], txt_file.filename)
        pdf_file.save(pdf_path)
        txt_file.save(txt_path)

        # 2. Ler TXT
        with open(txt_path, 'r', encoding='utf-8') as f:
            texto_total = f.read()

        # 3. Extrair Blocos (texto + páginas)
        blocos_com_paginas = extrair_blocos_decreto_com_paginas(texto_total)
        print(f"-> Blocos extraídos: {len(blocos_com_paginas)}")

        # 4. IDENTIFICAÇÃO GLOBAL DO PADRÃO
        tipo_global = identificar_padrao_global(texto_total)
        print(f"-> Padrão Global Identificado: {tipo_global}")

        # 5. Seleção do Processador (Switch-Case fora do loop)
        processador_ativo = None

        if tipo_global == "P3":
            processador_ativo = p3_processor
            print("-> Usando Motor: P3 (Anexo III)")
        elif tipo_global == "P2":
            processador_ativo = p2_processor
            print("-> Usando Motor: P2 (Orçamentário/REDU)")
        else:
            processador_ativo = p1_processor
            print("-> Usando Motor: P1 (Padrão)")

        resultados = []
        erros = []

        # 6. Loop de Processamento (Rápido e Direto)
        for i, (bloco, paginas_bloco) in enumerate(blocos_com_paginas):
            print(f"Processing Bloco {i+1}/{len(blocos_com_paginas)}...")

            dados = processador_ativo.processar_bloco(
                bloco,
                texto_total,
                pdf_path
            )

            if dados and "ERRO" not in dados:
                # Metadados auxiliares para anexos/galeria
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
                    "detalhe": detalhe
                })

        # 7. Finalização
        app.config['DADOS_EXTRAIDOS'] = resultados

        # Metadados globais para rota de imagens
        app.config['DECRETOS_META'] = {
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

        html = gerar_quadros_html(resultados, erros)

        # Limpeza apenas do TXT (mantém o PDF para visualização)
        try:
            os.remove(txt_path)
        except:
            pass

        has_results = len(resultados) > 0
        wrapper = f"<div id='upload-result' data-has-results='{'true' if has_results else 'false'}'>{html}</div>"
        return wrapper

    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"Erro: {e}", 500


@app.route("/download_q1")
def download_q1():
    dados = app.config.get('DADOS_EXTRAIDOS', [])
    df_q1, _ = gerar_quadros_dataframe(dados)
    output = BytesIO()
    df_q1.to_excel(output, index=False)
    output.seek(0)
    return send_file(output, as_attachment=True, download_name="quadro_1.xlsx")


@app.route("/download_q2")
def download_q2():
    dados = app.config.get('DADOS_EXTRAIDOS', [])
    _, df_q2 = gerar_quadros_dataframe(dados)
    output = BytesIO()
    df_q2.to_excel(output, index=False)
    output.seek(0)
    return send_file(output, as_attachment=True, download_name="quadro_2.xlsx")


@app.route("/decreto_imagens/<int:idx>")
def decreto_imagens(idx: int):
    """
    Galeria de imagens inline para um decreto específico (bloco),
    usada pelos links \"Ver anexos\" quando o decreto é de Excesso de Arrecadação.
    """
    meta = app.config.get("DECRETOS_META") or {}
    blocos_meta = meta.get("blocos") or []

    if idx < 0 or idx >= len(blocos_meta):
        return "Decreto não encontrado.", 404

    info = blocos_meta[idx]
    if not info.get("is_excesso"):
        return "Este decreto não é de Excesso de Arrecadação.", 400

    pdf_path = meta.get("pdf_path")
    paginas = info.get("pages") or []

    if not pdf_path or not paginas:
        return "Páginas do decreto não disponíveis.", 400

    imagens_html = []
    for page_num in paginas:
        imagens_html.append(
            f"<div style='margin-bottom: 24px;'>"
            f"<h4 style='margin-bottom: 8px;'>Página {page_num}</h4>"
            f"<img src='/decreto_imagens/raw/{idx}/{page_num}' alt='Página {page_num}' "
            f"style='max-width: 100%; height: auto; border: 1px solid #ddd; border-radius: 4px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);' />"
            f"</div>"
        )

    body = (
        "<!DOCTYPE html><html><head><meta charset='UTF-8'><title>Anexos do Decreto</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:800px;margin:20px auto;padding:20px;background:#f9f9f9;}"
        "h3{color:#2c3e50;} .galeria{max-height:70vh;overflow-y:auto;padding:10px;background:#fff;border-radius:8px;}"
        "a.btn{display:inline-block;margin-bottom:20px;padding:10px 16px;background:#3498db;color:#fff;text-decoration:none;border-radius:5px;font-weight:600;}"
        "a.btn:hover{background:#2980b9;}</style></head><body>"
        "<h3>Imagens do Decreto (Excesso de Arrecadação)</h3>"
        f"<a href='/decreto_imagens/pdf/{idx}' class='btn' download>Baixar PDF das páginas</a>"
        "<div class='galeria'>" + "".join(imagens_html) + "</div></body></html>"
    )
    return body


@app.route("/decreto_imagens/pdf/<int:idx>")
def decreto_imagens_pdf(idx: int):
    """
    Retorna um PDF contendo apenas as páginas do decreto (bloco) especificado.
    """
    import fitz  # PyMuPDF

    meta = app.config.get("DECRETOS_META") or {}
    blocos_meta = meta.get("blocos") or []

    if idx < 0 or idx >= len(blocos_meta):
        return "Decreto não encontrado.", 404

    info = blocos_meta[idx]
    if not info.get("is_excesso"):
        return "Este decreto não é de Excesso de Arrecadação.", 400

    pdf_path = meta.get("pdf_path")
    paginas = info.get("pages") or []

    if not pdf_path or not paginas or not os.path.exists(pdf_path):
        return "Páginas do decreto não disponíveis.", 400

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

        output = BytesIO(pdf_bytes)
        output.seek(0)
        numero = info.get("numero", "decreto")
        nome_arquivo = f"anexos_decreto_{numero}.pdf".replace("/", "-").replace(" ", "_")
        return send_file(output, mimetype="application/pdf", as_attachment=True, download_name=nome_arquivo)
    except Exception as e:
        return f"Erro ao gerar PDF: {e}", 500


@app.route("/decreto_imagens/raw/<int:idx>/<int:page_num>")
def decreto_imagem_raw(idx: int, page_num: int):
    """
    Retorna a imagem (JPEG) de uma página específica do PDF
    associada a um decreto/bloco.
    """
    import fitz  # PyMuPDF

    meta = app.config.get("DECRETOS_META") or {}
    blocos_meta = meta.get("blocos") or []

    if idx < 0 or idx >= len(blocos_meta):
        return "Decreto não encontrado.", 404

    info = blocos_meta[idx]
    paginas = info.get("pages") or []
    if page_num not in paginas:
        return "Página não associada a este decreto.", 400

    pdf_path = meta.get("pdf_path")
    if not pdf_path or not os.path.exists(pdf_path):
        return "Arquivo PDF não disponível.", 400

    try:
        doc = fitz.open(pdf_path)
        index = page_num - 1  # OCR é 1-based, PyMuPDF é 0-based
        if index < 0 or index >= len(doc):
            doc.close()
            return "Página fora do intervalo do PDF.", 400

        page = doc.load_page(index)
        pix = page.get_pixmap(matrix=fitz.Matrix(150 / 72, 150 / 72))
        img_bytes = pix.tobytes("jpeg")
        doc.close()

        output = BytesIO(img_bytes)
        output.seek(0)
        return send_file(output, mimetype="image/jpeg")
    except Exception as e:
        return f"Erro ao gerar imagem: {e}", 500


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
