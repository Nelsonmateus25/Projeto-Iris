# Projeto Iris:
<img width="1588" height="656" alt="image" src="https://github.com/user-attachments/assets/027ec0dd-f977-4680-9a21-02660b5e1635" />

## Aplicação Web que automatiza a extração e estruturação de dados de decretos municipais a partir de arquivos PDF.

O usuário envia um PDF com um ou mais decretos. A aplicação converte o documento em texto (via **Amazon Textract** ou TXT fornecido manualmente), classifica cada decreto em um padrão estrutural (P1, P2 ou P3), extrai os dados financeiros com a **API Gemini** do Google, e apresenta os resultados em tabelas interativas com opção de download em Excel.

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
