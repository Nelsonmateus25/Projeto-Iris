
import os
import time
import logging
import boto3
from collections import defaultdict
from dotenv import load_dotenv
from typing import List, Dict, Any, Optional

load_dotenv(override=True)

logger = logging.getLogger(__name__)

AWS_REGION = os.getenv("AWS_REGION", "us-east-2")
BUCKET_NAME = os.getenv("BUCKET_NAME", "projetoiris")

if not BUCKET_NAME or not AWS_REGION:
    logger.error(
        "BUCKET_NAME ou AWS_REGION não definidos. "
        "Verifique seu arquivo .env ou variáveis de ambiente."
    )


def upload_to_s3(file_path: str, bucket_name: str, s3_key: str) -> bool:
    """
    Faz o upload de um arquivo local para um bucket S3.

    Args:
        file_path: O caminho do arquivo local.
        bucket_name: O nome do bucket S3 de destino.
        s3_key: O "caminho" (chave) do objeto no S3.

    Returns:
        True se o upload foi bem-sucedido, False caso contrário.
    """
    s3 = boto3.client("s3", region_name=AWS_REGION)
    try:
        s3.upload_file(file_path, bucket_name, s3_key)
        logger.info("[Upload S3] Sucesso: '%s' enviado para 's3://%s/%s'", file_path, bucket_name, s3_key)
        return True
    except FileNotFoundError:
        logger.error("[Upload S3] Arquivo local não encontrado: %s", file_path)
        return False
    except Exception as e:
        logger.error("[Upload S3] Falha no upload: %s", e)
        logger.error("Verifique suas credenciais AWS e permissões do bucket.")
        return False


def delete_from_s3(bucket_name: str, s3_key: str) -> None:
    """
    Deleta um objeto de um bucket S3.

    Args:
        bucket_name: O nome do bucket S3.
        s3_key: A chave do objeto a ser deletado.
    """
    s3 = boto3.client("s3", region_name=AWS_REGION)
    try:
        s3.delete_object(Bucket=bucket_name, Key=s3_key)
        logger.info("[Cleanup S3] Sucesso: Objeto 's3://%s/%s' deletado.", bucket_name, s3_key)
    except Exception as e:
        logger.error("[Cleanup S3] Falha ao deletar: %s", e)


def extract_text_textract_s3(bucket_name: str, s3_key: str) -> str:
    """
    Inicia e monitora um job do Textract para extrair texto de um
    documento no S3.

    A lógica principal agrupa o texto por página e insere
    delimitadores [INÍCIO PAGINA X] e [FIM PAGINA X].

    Args:
        bucket_name: O nome do bucket S3 onde o documento está.
        s3_key: A chave do objeto do documento no S3.

    Returns:
        Uma string contendo o texto completo com delimitadores de página,
        ou uma string de erro (ex: "[Erro Textract S3]").
    """
    textract = boto3.client("textract", region_name=AWS_REGION)
    try:
        response = textract.start_document_text_detection(
            DocumentLocation={"S3Object": {
                "Bucket": bucket_name, "Name": s3_key}}
        )
        job_id = response["JobId"]
        logger.info("[Textract] Job iniciado: %s", job_id)
    except Exception as e:
        logger.error("[Textract] Falha ao iniciar o job: %s", e)
        return "[Erro Textract S3: Falha ao iniciar]"

    result: Dict[str, Any] = {}
    while True:
        try:
            result = textract.get_document_text_detection(JobId=job_id)
            status = result["JobStatus"]
            logger.info("[Textract] Status: %s", status)
            if status in ["SUCCEEDED", "FAILED"]:
                break
            time.sleep(3)
        except Exception as e:
            logger.error("[Textract] Falha ao obter status do job: %s", e)
            return "[Erro Textract S3: Falha no get_status]"

    if result["JobStatus"] == "SUCCEEDED":
        # Etapa 1: Coletar TODOS os blocos, tratando a paginação da API Textract
        blocks = result.get("Blocks", [])
        next_token = result.get("NextToken")

        while next_token:
            logger.info("[Textract] Buscando mais resultados (NextToken)...")
            response_pag = textract.get_document_text_detection(
                JobId=job_id, NextToken=next_token
            )
            blocks.extend(response_pag.get("Blocks", []))
            next_token = response_pag.get("NextToken")

        # --- LÓGICA CENTRAL (INTOCADA, CONFORME SOLICITADO) ---
        logger.info("[Textract] Processando e agrupando blocos por página...")

        # Etapa 2: Agrupar todas as linhas de texto por seu número de página
        pages_content = defaultdict(list)

        for block in blocks:
            if block["BlockType"] == "LINE":
                page_num = block["Page"]
                pages_content[page_num].append(block["Text"])

        # Etapa 3: Construir a string final, ordenada por página
        full_text_parts = []
        for page_num in sorted(pages_content.keys()):
            full_text_parts.append(f"[INÍCIO PAGINA {page_num}]\n")
            page_text = "\n".join(pages_content[page_num])
            full_text_parts.append(page_text)
            full_text_parts.append(f"\n[FIM PAGINA {page_num}]")

        full_text = "\n".join(full_text_parts)
        # --- FIM DA LÓGICA CENTRAL ---

        return full_text

    logger.error("[Textract] Job falhou: %s. Detalhes: %s", job_id, result.get('StatusMessage'))
    return "[Erro Textract S3: Job FAILED]"


# ---FUNÇÃO ORQUESTRADORA ---

def processar_pdf_s3(
    local_pdf_path: str,
    s3_prefix: str = "textract-uploads/",
    output_dir: str = "txt_delimitado",
    cleanup_s3: bool = False,
) -> Optional[str]:
    """
    Orquestra o processo completo de conversão de PDF para TXT.

    1. Gera os caminhos de S3 e de saída.
    2. Cria o diretório de saída 'txt_delimitado'.
    3. Faz o upload do PDF para o S3.
    4. Executa a extração do Textract.
    5. Salva o .txt resultante no diretório de saída.
    6. (Opcional) Limpa o arquivo do S3.

    Args:
        local_pdf_path: Caminho para o arquivo PDF local.
        s3_prefix: O prefixo (pasta) no S3 para onde o PDF será enviado.
        output_dir: A pasta local onde o .txt será salvo.
        cleanup_s3: Se True, deleta o arquivo do S3 após a extração.

    Returns:
        O caminho do arquivo .txt salvo em caso de sucesso, ou None.
    """
    logger.info("--- Iniciando processamento para: %s ---", local_pdf_path)

    # 1. Gerar nomes de arquivo e S3 key
    base_name = os.path.basename(local_pdf_path)
    file_name, _ = os.path.splitext(base_name)

    # Garante que a S3 key use barras "/"
    s3_key = os.path.join(s3_prefix, base_name).replace("\\", "/")

    # Gera o caminho de saída conforme solicitado
    output_txt_path = os.path.join(output_dir, f"{file_name}.txt")

    # 2. Criar diretório de saída
    try:
        os.makedirs(output_dir, exist_ok=True)
        logger.info("[IO] Diretório de saída '%s' garantido.", output_dir)
    except Exception as e:
        logger.error("[IO] Não foi possível criar o diretório '%s': %s", output_dir, e)
        return None

    # 3. Upload para S3
    if not upload_to_s3(local_pdf_path, BUCKET_NAME, s3_key):
        logger.error("[Falha] Upload do arquivo %s falhou. Abortando.", local_pdf_path)
        return None

    # 4. Execução do Textract
    logger.info("--- INICIANDO EXTRAÇÃO COM TEXTRACT (isso pode levar alguns minutos...) ---")
    start_time = time.time()
    extracted_text = extract_text_textract_s3(BUCKET_NAME, s3_key)
    end_time = time.time()
    logger.info("--- EXTRAÇÃO CONCLUÍDA (Levou %.2f segundos) ---", end_time - start_time)

    # 5. Validação e Salvamento do .txt
    if "[Erro Textract S3]" in extracted_text or not extracted_text:
        logger.error("[Falha] Ocorreu um erro no Textract. Nenhum arquivo .txt será salvo.")
        if cleanup_s3:
            delete_from_s3(BUCKET_NAME, s3_key)
        return None

    try:
        with open(output_txt_path, "w", encoding="utf-8") as f:
            f.write(extracted_text)
        logger.info("[SUCESSO] Saída completa salva em: '%s'", output_txt_path)

        # 6. (Opcional) Cleanup
        if cleanup_s3:
            delete_from_s3(BUCKET_NAME, s3_key)

        return output_txt_path

    except Exception as e:
        logger.error("[IO] Não foi possível salvar o arquivo .txt em '%s': %s", output_txt_path, e)
        return None


# --- Bloco de Teste Principal ---
if __name__ == "__main__":
    """
    Este bloco é executado apenas quando o script é chamado
    diretamente (ex: `python utils/pdf_to_txt.py`).

    Ele serve como um teste rápido para a função `processar_pdf_s3`.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    logger.info("--- INICIANDO SCRIPT DE TESTE (pdf_to_txt.py) ---")

    # 1. Definições
    # !! IMPORTANTE !!
    # Altere este caminho para o PDF que você quer testar
    # Use caminhos relativos ou absolutos.
    local_file_to_test = "Decretos 2023/01.Acopiara 2023/Decretos acopiara.pdf"
    s3_prefix_for_test = "testes/processador/"  # Pasta de testes no S3

    # 2. Execução da função principal
    if not os.path.exists(local_file_to_test):
        logger.error("[Teste] Arquivo de teste não encontrado em: '%s'", local_file_to_test)
        logger.error("Atualize a variável 'local_file_to_test' no bloco __main__.")
    else:
        # cleanup_s3=False (Padrão) -> Mantém o arquivo no S3 para debugging
        # cleanup_s3=True -> Deleta o arquivo do S3 após o processo
        caminho_do_txt = processar_pdf_s3(
            local_file_to_test,
            s3_prefix_for_test,
            cleanup_s3=False
        )

        if caminho_do_txt:
            logger.info("[Teste OK] Processamento concluído. Arquivo: %s", caminho_do_txt)
        else:
            logger.error("[Teste FALHOU] Processamento falhou.")

    logger.info("--- SCRIPT DE TESTE FINALIZADO ---")
