# Analisador de Decretos

Aplicação web construída com **Streamlit** que automatiza a extração e estruturação de dados de decretos municipais de crédito adicional a partir de arquivos PDF.

O usuário envia um PDF com um ou mais decretos. A aplicação converte o documento em texto (via **Amazon Textract** ou TXT fornecido manualmente), classifica cada decreto em um padrão estrutural (P1, P2 ou P3), extrai os dados financeiros com a API Gemini do Google, e apresenta os resultados em tabelas interativas com opção de download em Excel.

---

## Fluxo da aplicação

```
Usuário envia PDF
        │
        ▼
┌───────────────────┐      TXT fornecido?
│  app_streamlit.py │ ──── Sim ──► usa texto diretamente
│  (orquestrador)   │
└───────────────────┘      Não  ──► Amazon Textract (S3)
        │                              extrai texto com paginação
        ▼
┌───────────────────────────┐
│  processamento_txt.py     │
│  - identifica padrão global (P1 / P2 / P3)
│  - extrai blocos de decreto com páginas associadas
└───────────────────────────┘
        │
        ├── P1 ──► P1Processor  (Gemini: extração textual + segmentação veto)
        ├── P2 ──► P2Processor  (Gemini: segmentação REDU/Artigos)
        └── P3 ──► P3Processor  (Gemini: corpo + Anexo III mesclados)
                │
                ▼
        formatters.py  (formatar_valor, formatar_data)
                │
                ▼
        Resultados no session_state
                │
        ┌───────┴────────┐
        │                │
    Quadros HTML    Downloads Excel
    (Quadro 1 + 2)  (quadro_1.xlsx, quadro_2.xlsx)
        │
    Galeria de Anexos (decretos de Excesso de Arrecadação)
    imagens das páginas do PDF via PyMuPDF
```

---

## Pré-requisitos

| Requisito | Versão mínima | Para que serve |
|---|---|---|
| Python | 3.10+ | Sintaxe `str \| None` usada nos módulos |
| Conta Google AI Studio | — | Chave da API Gemini |
| Conta AWS | — | Amazon Textract + bucket S3 |
| Git | 2.x | Controle de versão |

---

## Estrutura de arquivos

```
projeto iris/
│
├── app_streamlit.py          # Ponto de entrada da aplicação Streamlit
│                             # Contém: UI, orquestração, geração de relatórios
│
├── assets/
│   └── style.css             # Estilos personalizados (tabelas, galeria, botão azul)
│
├── utils/
│   ├── __init__.py           # Marca utils/ como pacote Python
│   ├── formatters.py         # Funções formatar_valor() e formatar_data() (DRY)
│   ├── processamento_txt.py  # Extração de blocos e classificação de padrão global
│   ├── p1_processamento.py   # Processador para decretos Padrão 1 (genérico)
│   ├── p2_processamento.py   # Processador para decretos Padrão 2 (orçamentário REDU)
│   ├── p3_processamento.py   # Processador para decretos Padrão 3 (com Anexo III)
│   └── pdf_to_txt.py         # Upload S3 + extração Textract + limpeza
│
├── .env                      # Variáveis de ambiente (NÃO versionar — ver .gitignore)
├── .gitignore                # Ignora .env, uploads/, __pycache__, etc.
├── requirements.txt          # Dependências Python
└── README.md                 # Este arquivo
```

---

## Variáveis de ambiente

Crie um arquivo `.env` na raiz do projeto com as variáveis abaixo. **Nunca suba este arquivo para o GitHub.**

```env
# Google Gemini
GEMINI_API_KEY=sua_chave_aqui
GEMINI_MODEL=gemini-2.5-flash   # opcional, este é o padrão

# AWS (necessário para processar PDFs sem TXT)
AWS_REGION=us-east-2
BUCKET_NAME=nome-do-seu-bucket
AWS_ACCESS_KEY_ID=sua_access_key
AWS_SECRET_ACCESS_KEY=sua_secret_key
```

| Variável | Obrigatória | Descrição |
|---|---|---|
| `GEMINI_API_KEY` | Sim | Chave da API Google Gemini |
| `GEMINI_MODEL` | Não | Modelo Gemini a usar (padrão: `gemini-2.5-flash`) |
| `AWS_REGION` | Sim (sem TXT) | Região AWS do bucket S3 e Textract |
| `BUCKET_NAME` | Sim (sem TXT) | Nome do bucket S3 para upload temporário |
| `AWS_ACCESS_KEY_ID` | Sim (sem TXT) | Credencial AWS |
| `AWS_SECRET_ACCESS_KEY` | Sim (sem TXT) | Credencial AWS |

> As variáveis AWS só são necessárias se você processar PDFs sem fornecer um TXT manualmente.

---

## Instalação e execução local

### 1. Clonar o repositório

```bash
git clone https://github.com/SEU_USUARIO/SEU_REPOSITORIO.git
cd "projeto iris"
```

### 2. Criar e ativar o ambiente virtual

```bash
# Criar o venv
python -m venv venv

# Ativar no macOS/Linux
source venv/bin/activate

# Ativar no Windows
venv\Scripts\activate
```

### 3. Instalar as dependências

```bash
pip install -r requirements.txt
```

### 4. Criar o arquivo `.env`

Copie o modelo abaixo para um arquivo `.env` na raiz e preencha com suas credenciais:

```env
GEMINI_API_KEY=
GEMINI_MODEL=gemini-2.5-flash
AWS_REGION=us-east-2
BUCKET_NAME=
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
```

### 5. Executar a aplicação

```bash
streamlit run app_streamlit.py
```

A aplicação abrirá automaticamente no navegador em `http://localhost:8501`.

---

## Como usar

1. **Enviar PDF:** Clique em "Arquivo PDF Original (.pdf)" e selecione o arquivo com os decretos.
2. **TXT opcional:** Se você já tiver o texto extraído (para debug ou economia de custo AWS), envie o arquivo `.txt`. Caso contrário, o sistema usará o Amazon Textract automaticamente.
3. **Analisar:** Clique no botão **Analisar**. Uma barra de progresso indica o andamento bloco a bloco.
4. **Ver resultados:**
   - **Quadro de Erros e Alertas:** lista qualquer decreto que não pôde ser processado.
   - **Quadro 1:** resumo de todos os decretos extraídos com sucesso.
   - **Quadro 2:** detalhamento por fonte de recurso (quando há mais de uma fonte por decreto).
5. **Baixar Excel:** Botões "Baixar Quadro 1" e "Baixar Quadro 2" exportam os dados em `.xlsx`.
6. **Anexos (Excesso de Arrecadação):** Para decretos classificados como Excesso de Arrecadação, uma seção adicional exibe a galeria de páginas do PDF e um botão para baixar as páginas em PDF.

---

## Padrões de decreto reconhecidos

A aplicação identifica automaticamente o padrão estrutural do arquivo e usa o processador correspondente:

| Padrão | Identificação | Processador |
|---|---|---|
| **P1** — Genérico | Cabeçalho `DECRETO Nº XXXX/XX` | `P1Processor` — segmentação com lógica de veto de ambiguidade |
| **P2** — Orçamentário | Cabeçalho `Decreto Orçamentário N°` ou marcador `REDU.` | `P2Processor` — segmentação por marcadores REDU/Artigos |
| **P3** — Com Anexo III | Presença de `ANEXO III` ou `ANEXO 3` no texto | `P3Processor` — mescla corpo + dados financeiros do Anexo |

---

## Descrição dos módulos

### `utils/formatters.py`
Funções puras de formatação, centralizadas (princípio DRY):
- `formatar_valor(valor_str)` — converte strings monetárias para o padrão brasileiro (ex: `"1234567.89"` → `"1.234.567,89"`).
- `formatar_data(data_str)` — normaliza datas para o formato por extenso em português (ex: `"01/03/2023"` → `"1 de Março de 2023"`).

### `utils/processamento_txt.py`
Análise do texto OCR completo:
- `identificar_padrao_global(texto)` — determina se o arquivo inteiro é P1, P2 ou P3.
- `extrair_blocos_decreto_com_paginas(texto)` — divide o texto em blocos individuais por decreto e mapeia quais páginas do PDF correspondem a cada bloco.

### `utils/p1_processamento.py` / `p2_processamento.py` / `p3_processamento.py`
Cada processador implementa:
- `processar_bloco(bloco, texto_total_ocr, caminho_pdf)` — método principal que segmenta o texto, chama a API Gemini com um prompt específico para o padrão, e aplica pós-processamento (formatação de datas e valores).
- `_call_gemini(prompt, contexto)` — chama a API com retry automático (até 3 tentativas) e parse do JSON retornado.

### `utils/pdf_to_txt.py`
Integração com AWS:
- `upload_to_s3(file_path, bucket, key)` — envia o PDF para o S3.
- `extract_text_textract_s3(bucket, key)` — inicia e monitora o job do Textract, agrupa o texto por página com delimitadores `[INÍCIO PAGINA X]` / `[FIM PAGINA X]`.
- `delete_from_s3(bucket, key)` — limpa o arquivo temporário do S3 após a extração.

---

## Deploy no Streamlit Cloud

### 1. Subir o código para o GitHub

O repositório **pode ser público** — as credenciais ficam nos Secrets do Streamlit Cloud, nunca no código.

```bash
git add .
git commit -m "Deploy inicial"
git push origin main
```

### 2. Configurar o app no Streamlit Cloud

1. Acesse [share.streamlit.io](https://share.streamlit.io) e conecte sua conta GitHub.
2. Clique em **New app**.
3. Selecione o repositório, a branch (`main`) e o arquivo principal (`app_streamlit.py`).
4. Clique em **Advanced settings** → **Secrets**.
5. Cole as variáveis de ambiente no formato TOML:

```toml
GEMINI_API_KEY = "sua_chave_aqui"
GEMINI_MODEL = "gemini-2.5-flash"
AWS_REGION = "us-east-2"
BUCKET_NAME = "nome-do-seu-bucket"
AWS_ACCESS_KEY_ID = "sua_access_key"
AWS_SECRET_ACCESS_KEY = "sua_secret_key"
```

6. Clique em **Deploy**.

> O Streamlit Cloud lê os Secrets como variáveis de ambiente, exatamente como o `python-dotenv` lê o `.env` localmente. O código não precisa de nenhuma alteração para funcionar nos dois ambientes.

---

## Branches do repositório

| Branch | Propósito |
|---|---|
| `main` | Versão estável para produção e deploy no Streamlit Cloud |
| `2026-03-11-o4nu` | Branch de desenvolvimento ativo com a versão Streamlit refatorada |
| `testes` | Branch com apenas os módulos `utils/` para testes isolados dos processadores |

---

## Dependências

| Pacote | Para que serve |
|---|---|
| `streamlit` | Framework da interface web |
| `python-dotenv` | Carregamento do `.env` em desenvolvimento local |
| `google-generativeai` | Cliente da API Google Gemini |
| `pandas` | Manipulação de dados e geração dos DataFrames para Excel |
| `openpyxl` | Motor de escrita de arquivos `.xlsx` (usado pelo pandas) |
| `PyMuPDF` | Extração de imagens de páginas do PDF para a galeria de anexos |
| `boto3` | SDK AWS para upload S3 e chamadas ao Textract |
| `markdown` | Utilitário de renderização Markdown (suporte auxiliar) |

---

## Considerações de segurança

- O arquivo `.env` está no `.gitignore` e **nunca deve ser commitado**.
- Em produção (Streamlit Cloud), as credenciais são gerenciadas pelos **Secrets** da plataforma.
- Os PDFs enviados pelo usuário são salvos em arquivos temporários do sistema operacional (`/tmp`) com nomes únicos gerados por **UUID**, evitando colisões entre usuários simultâneos. Os arquivos temporários são removidos com `os.unlink()` ao final do processamento.
- As chaves S3 para upload temporário no Textract também incluem UUID para evitar sobrescrita entre sessões concorrentes.
