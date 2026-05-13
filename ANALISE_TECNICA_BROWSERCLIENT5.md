# Análise Técnica Profunda — `example_browserclient5`

> Análise da implementação atual do "copilot de reuniões" baseado em RealtimeSTT (fork de `KoljaB/RealtimeSTT`). Documento de engenharia, sem alterações de código nesta etapa.
>
> **Repo:** `C:\wamp64\www\RealtimeSTT` · **Variante ativa:** `example_browserclient5` · **Data:** 2026-05-08

---

## 1. Visão Geral da Aplicação

### Propósito
A aplicação é um **copilot de conversação em tempo real** desenhado para que o Evaldo (falante não-nativo de inglês, fundador da 908 AI) consiga participar de reuniões de networking, calls comerciais e entrevistas de emprego com suporte de IA que **fala como ele**. O tom do output é deliberadamente em primeira pessoa — confirmado pelo `system_role` em `setup_data.json` ("Speak as if you are me ... since I'm Brazilian and my English is not perfect").

Não é um transcritor de reuniões nem um assistente que sintetiza atas. É um **"prompt em tempo real"** — o usuário ouve a contraparte falar, vê a transcrição da contraparte na tela, escolhe um trecho (ou os últimos N trechos), e a IA produz uma resposta sugerida que ele lê/parafraseia.

### Fluxo do usuário (estado atual)
1. **Setup (uma vez por contexto):** o usuário abre `setup.php` no WAMP, preenche `system_role` + `additional_info` (persona, currículo, contexto da reunião) → `save_setup.php` grava em `setup_data.json`.
2. **Inicialização:** roda `start_server.bat` (que **está apontando para o diretório errado** — `example_browserclient3` — bug histórico não corrigido) e abre `index.html` no browser.
3. **Captura de áudio:** o **servidor Python** captura áudio direto do microfone do PC via PyAudio (`recorder_config['use_microphone'] = True` em `server.py:37`). O cliente HTML **não captura áudio** — ao contrário do `example_browserclient` original.
4. **Transcrição:** RealtimeSTT roda Whisper `large-v2` em uma thread dedicada (`recorder_thread`, `server.py:51`). VAD híbrido (Silero + WebRTC). `enable_realtime_transcription = False` — só emite frases completas, não tokens incrementais.
5. **Push ao cliente:** cada frase finalizada vira `{type: 'fullSentence', text}` via WebSocket → o cliente prepend a frase na lista (`client.js:71`, `unshift` + `insertBefore`).
6. **Ações do usuário:**
   - Clicar em qualquer frase → envia aquela frase isolada ao Groq.
   - Clicar nos botões `2..8` → envia as últimas N frases concatenadas como contexto.
   - Clicar 📷 → captura screenshot de uma região do desktop (bbox hardcoded `0,140,2200,1820`) → envia para um modelo multimodal (Llama-4 Scout) com prompt fixo de análise de código.
7. **Resposta:** Groq retorna não-stream (`stream=False`), o cliente prepend o texto na sidebar `#chatResponses`.

### Componentes principais
| Componente | Arquivo | Papel |
|---|---|---|
| **Servidor WS + STT + LLM bridge** | `server.py` | Loop de gravação, websocket única, despache para Groq |
| **Cliente UI** | `index.html` + `client.js` | Lista de frases, botões de contexto, sidebar de respostas |
| **Setup (HTTP separado, via WAMP)** | `setup.php` + `save_setup.php` | Form que escreve `setup_data.json` |
| **Persistência de persona** | `setup_data.json`, `setup_data_job.json` | Dois presets prontos (networking meeting / job interview) |
| **Boot** | `start_server.bat` | Ativa venv + lança Python + abre URL (com paths errados) |

### Como a transcrição em tempo real é tratada
- **Modelo principal:** `large-v2` (CPU/GPU dependente do build do faster-whisper). Modelo "realtime" (`tiny.en`) está configurado mas **desativado** (`enable_realtime_transcription: False`).
- **VAD:** Silero (`sensitivity 0.4`) + WebRTC (`sensitivity 2`) com `post_speech_silence_duration: 0.1s`.
- **Min length / gap = 0:** sem agregação mínima → tende a fragmentar utterances curtas.
- **Realtime stabilizado:** o callback `on_realtime_transcription_stabilized = text_detected` está cabeado, mas como `enable_realtime_transcription=False`, ele **nunca dispara** — código morto.
- O loop `while True: full_sentence = recorder.text()` em `recorder_thread` é o único caminho real de saída de texto.

### Como os botões/context actions funcionam
- O array `fullSentences` mantém o histórico completo da sessão **na memória do browser** (sem TTL, sem persistência).
- `sendLastNSentences(n)` faz `fullSentences.slice(0, n).join(' ')` — observe o detalhe: `slice(0, n)` no array retorna os **N primeiros** (porque `unshift` joga no topo), o que é equivalente aos N últimos cronologicamente. É correto, mas frágil — a semântica depende do invariante "sempre prepend".
- Cada clique vira um `{type: 'groq', text}` enviado pela mesma WS de áudio.

### Como `system_role` e `additional_info` influenciam
Em `handle_groq_request` (`server.py:188`), a montagem do prompt é:
```
[
  {role: "system",  content: system_role},
  {role: "user",    content: "This is the crucial information that you need to know:" + additional_info},
  {role: "user",    content: "I need you to answer this as me:" + user_message}
]
```
Três efeitos práticos:
1. `system_role` define **persona + estilo + tom** (ex.: "responda simples porque meu inglês não é perfeito").
2. `additional_info` é um **dossiê estático** (currículo, contexto da empresa, briefing).
3. A frase clicada vira o **gatilho contextual** prefixado por "answer this as me:".

> **Limitação importante:** não há **memória de turnos**. Cada clique é stateless. O `example_browserclient3` chegou a ter um `history[]` rudimentar com janela de tokens, mas isso foi **removido** no v5.

---

## 2. Arquitetura Atual

### Mapa de módulos

```
┌─────────────────────────── BROWSER ───────────────────────────┐
│                                                                │
│  index.html      ┌─────────────┐         ┌──────────────────┐ │
│  ┌──────────┐    │ #textDisplay │ ←───── │  fullSentences[] │ │
│  │  main    │    │ (transcript) │        │   (in-memory)    │ │
│  └──────────┘    └─────────────┘        └──────────────────┘ │
│                          ▲                       ▲             │
│                          │ click(sentence)       │             │
│                          ▼                       │             │
│                    ┌─────────────┐               │             │
│                    │  client.js  │ ──────────────┘             │
│                    └─────┬───────┘                             │
│                          │  WebSocket("ws://localhost:8001")   │
└──────────────────────────┼─────────────────────────────────────┘
                           │  (JSON: 'groq'|'screenshot'  ▲      │
                           │   ↑ cliente → servidor)      │      │
                           │  (JSON: 'fullSentence'|...   │      │
                           │   ↓ servidor → cliente)      │      │
                           ▼                              │      │
┌──────────────────── PYTHON SERVER (server.py) ─────────────────┐
│                                                                │
│   asyncio loop (websockets.serve, port 8001)                   │
│   ┌────────────────────────────────────────────────────────┐   │
│   │ async echo(ws):                                        │   │
│   │   global client_websocket = ws  ← single-client only   │   │
│   │   for msg in ws:                                       │   │
│   │     if json: dispatch (groq | screenshot)              │   │
│   │     else: (audio binary path — DEAD CODE em v5)        │   │
│   └────────────────────────────────────────────────────────┘   │
│                          ▲                                     │
│        send_to_client(json)│                                   │
│                          │                                     │
│   ┌──────────────────────┴─────────────────────────────────┐   │
│   │  recorder_thread (threading.Thread):                   │   │
│   │    AudioToTextRecorder(use_microphone=True, large-v2)  │   │
│   │    while True:                                         │   │
│   │      full_sentence = recorder.text()  ← bloqueante     │   │
│   │      asyncio.new_event_loop()         ← ANTI-PATTERN   │   │
│   │        .run_until_complete(send_to_client({…}))        │   │
│   └────────────────────────────────────────────────────────┘   │
│                                                                │
│   ┌──────────────────────────────────────────────────────────┐ │
│   │  handle_groq_request(text):                              │ │
│   │    system_role, additional_info = load_setup_data()      │ │
│   │    Groq.chat.completions.create(stream=False)            │ │
│   │      model = llama-3.3-70b-versatile                     │ │
│   │    → ws.send({type:'groqResponse', response})            │ │
│   └──────────────────────────────────────────────────────────┘ │
│                                                                │
│   ┌──────────────────────────────────────────────────────────┐ │
│   │  handle_screenshot_request():                            │ │
│   │    PIL.ImageGrab.grab(bbox=(0,140,2200,1820)) → JPEG     │ │
│   │    base64 → Groq vision (llama-4-scout-17b-16e-instruct) │ │
│   │    prompt fixo: "explain code if any"                    │ │
│   └──────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────┘

┌─────────────────────── WAMP / PHP (porta 80) ───────────────────┐
│  setup.php  → POST /save_setup.php  → setup_data.json (disk)   │
│  Lido pelo server.py em todo handle_*_request (re-read on hit) │
└────────────────────────────────────────────────────────────────┘
```

### Detalhamento por camada

- **Frontend:** vanilla JS + HTML + CSS inline. Sem framework. Sem build step. `socket.io.min.js` é incluído mas **não usado** (resíduo) — a app usa `WebSocket` nativo.
- **Backend:** Python 3.x, `websockets` (legacy API: `websockets.legacy.server` evidenciado no traceback do log), `asyncio`, `threading.Thread` para o recorder.
- **Streaming:** binário customizado `[uint32 len_meta][meta_json][pcm16]` na rota de áudio — **não é mais usado** em v5 porque a captura é server-side. O parser ainda existe (`server.py:99-105`) mas é código morto.
- **WebSocket / events:** uma única WS, sem rooms, sem auth, sem heartbeat, sem reconexão server-side. O cliente v5 também não tenta reconectar (a versão 1 tinha um `setInterval` para reconectar; foi removido).
- **Transcrição:** RealtimeSTT (Whisper large-v2) em thread separada. Output é frase finalizada (`recorder.text()` — bloqueante até o VAD detectar fim de fala).
- **Gerenciamento de contexto:** **ausente**. Todo o "contexto" é (1) `setup_data.json` estático + (2) o slice atual selecionado pelo usuário.
- **Prompts:** templates string-concat hardcoded em `server.py:152-153, 204-206`.
- **Estado da aplicação:**
  - **Servidor:** `client_websocket` (global, único), `recorder` (global, único), `recorder_ready` (Event).
  - **Cliente:** `fullSentences[]` em `client.js`. Perdido em refresh.
- **Comunicação entre módulos:** PHP escreve JSON em disco, Python lê em cada request. Não há IPC nem watcher — re-leitura síncrona.
- **Persistência:** apenas `setup_data.json`. Nada de transcripts, respostas, sessões, métricas.

---

## 3. Diferenças entre o Fork Original e Nossa Versão

### Cronologia das variantes neste repo
- `example_browserclient/` (upstream, intacto): captura de áudio **no browser** (`getUserMedia`), envia PCM 16-bit via WS, server-side recorder com `use_microphone=False` + `feed_audio`. Reconexão automática a cada 5 s. Apenas mostra transcript — sem LLM.
- `example_browserclient2/`: primeira tentativa (mantém arquitetura upstream, sem LLM ainda).
- `example_browserclient3/`: introduz **Groq** + envio automático de cada frase ao LLM + **`history[]` com janela de tokens** + `is_incomplete_sentence()` heurístico que marca respostas incompletas em cinza.
- `example_browserclient4/`: introduz **`setup.php` + `setup_data.json`** (system_role + additional_info externalizados).
- `example_browserclient5/`: combina v4 (setup persistido) + **clique em frase** + **botões 2..8** + **screenshot** + remove `history[]` + ativa **`use_microphone=True`** (server-side audio).

### Funcionalidades adicionadas (v5 vs upstream)
| # | Feature | Onde |
|---|---|---|
| 1 | Persona configurável (`system_role` + `additional_info`) | `setup.php`, `setup_data.json` |
| 2 | Clique em frase para gerar resposta como o usuário | `client.js:15-17`, `server.py:188` |
| 3 | Botões 2..8 para contexto multi-frase | `client.js:81-86` |
| 4 | Screenshot → Vision LLM | `client.js:56-61`, `server.py:130-185` |
| 5 | Captura de áudio server-side (mic local) | `server.py:37` |
| 6 | UI sidebar separada para respostas | `index.html` + CSS |
| 7 | Ordem reversa (mais recente no topo) | `client.js:20, 46` (`insertBefore`) |

### Mudanças arquiteturais
- **Captura de áudio mudou de browser → servidor.** Boa para latência local e simplicidade, **terrível** para deployment remoto, multi-cliente, mobile, ou ambientes sem mic disponível ao processo Python.
- **Modelo de mensagens evoluiu** de "audio binário apenas" para **multiplexed** (audio + JSON). A demuxing usa `try/except json.JSONDecodeError`, o que é frágil mas funciona.
- **Histórico de conversa foi removido** entre v3 e v5 — uma regressão se o objetivo é continuidade de contexto.

### Hacks / workarounds detectados
1. `asyncio.new_event_loop().run_until_complete(...)` chamado de uma thread síncrona em **todo callback** (`server.py:25, 59`). Cria um novo loop por evento — anti-pattern severo.
2. `bbox=(0, 140, 2200, 1820)` hardcoded no screenshot — depende do monitor específico do Evaldo.
3. Path no `start_server.bat` aponta para `example_browserclient3` (velho) e abre URL `example_browserclient4/` (também velho, não é v5).
4. `socket.io.min.js` carregado mas não usado.
5. Código de demux de áudio binário em `server.py:99-105` é código morto em v5.
6. Callback `on_realtime_transcription_stabilized` cabeado para uma feature **desativada** (`enable_realtime_transcription=False`).
7. API key da Groq **hardcoded em texto plano** (`server.py:18`) — está commitado no git.
8. PHP grava JSON; Python relê o arquivo a cada chamada (`load_setup_data` é chamada dentro de `handle_groq_request` e `handle_screenshot_request`). I/O desnecessário.

### Riscos técnicos
- **Vazamento da API key Groq** já está exposta no histórico do git deste repo.
- **Race condition** no `client_websocket` global: se um cliente reconecta enquanto a thread do recorder está enviando, há possibilidade de envio para socket fechado.
- **Single-client only:** se você abrir duas abas, a segunda sobrescreve `client_websocket` e a primeira deixa de receber.
- **Recorder bloqueante:** o `recorder.text()` é bloqueante; o thread não tem como ser interrompido limpo. Não há shutdown gracioso.
- **Loop por evento:** `asyncio.new_event_loop()` por callback pode acumular timers/handles e travar no Windows com ProactorEventLoop em casos patológicos.
- **`stream=False`:** o usuário espera 1–4 s pela resposta inteira. Em uma reunião isso é **inviável** — a sentença que o usuário deveria estar respondendo já passou.
- **Sem WSS, sem auth, sem CORS:** um colega no mesmo Wi-Fi pode conectar `ws://<seu-ip>:8001` e injetar prompts.

### Áreas frágeis
- `client.js:71` (`unshift`) + `slice(0, n).join(' ')` — depende do invariante "sempre prepend"; se um dia alguém trocar para `appendChild`, a função "últimas N" passa a retornar as primeiras N.
- O `screenshot.jpg` é regravado no diretório do projeto — **risco de aparecer no git** (já vi um arquivo de 107 KB lá agora).
- O `realtimesst.log` já demonstra que o sistema falha em runtime com `model_decommissioned` e isso quebra a connection handler inteira da `websockets` (o stack trace mostra `connection handler failed`) — uma única chamada falha pode derrubar a conexão.

### O que vale manter
- A separação **persona (setup_data.json)** + **frase clicável** — UX é o diferencial real do produto.
- Layout **transcript à esquerda / chat à direita** com responses em ordem reversa.
- Use of Groq — latência baixa de inferência é apropriada.
- Captura server-side com `large-v2` quando o usuário usa o próprio PC — qualidade alta.

### O que precisa refatorar (preserve a UX, troque o motor)
- Modelo de concorrência (asyncio + thread + new_event_loop por evento).
- Single-client global → connection manager.
- API key hardcoded → variável de ambiente / dotenv.
- `stream=False` → streaming server-side de tokens com SSE/WS.
- Demux JSON-vs-binário com `try/except` → mensagem com header de tipo.
- Setup via PHP + arquivo → endpoint nativo Python (FastAPI).
- `start_server.bat` quebrado → corrigir caminhos.

### O que pode ser removido
- Código de demux de áudio binário (server.py:99-105) — não usado em v5.
- `on_realtime_transcription_stabilized` — feature desativada.
- `socket.io.min.js` no `<head>` — não usado.
- `test.py` — script local de teste de screenshot.
- `example_browserclient2/3/4/` — ou archivar em `legacy/` para não confundir.
- `setup_data_job.json` — ou padronizar como "preset" formal com seletor de persona na UI.

---

## 4. Avaliação Técnica Profunda

### Qualidade da arquitetura: **3/10**
Single-file server com globals, thread bloqueante, event loop ad-hoc por callback. Funciona como protótipo, **não escala** nem horizontalmente nem para mais de um cliente. Sem testes, sem injeção de dependências, sem camada de domínio.

### Escalabilidade: **2/10**
- Single-client por design (`client_websocket = ws`).
- Single-recorder por design (mic local; impossível paralelizar).
- Sem fila/buffer entre captura e envio.
- Para escalar é preciso **separar** "captura/STT" de "orquestração de prompts" (atualmente acoplados no mesmo processo).

### Latência: **5/10**
- **Captura → frase pronta:** dependente do `post_speech_silence_duration=0.1s` (ok) + tempo de inferência do `large-v2` (~300 ms a 2 s no CPU; ~150 ms na GPU).
- **Clique → resposta:** Groq `llama-3.3-70b-versatile` → 700 ms a 2 s para resposta completa porque **stream=False**.
- **Total typical worst case:** ~3-4 s entre o usuário clicar e ver a resposta. Para uma reunião ao vivo é o limite do tolerável.
- **Tipping point:** habilitar streaming reduz em 50-70% o "perceived latency".

### Concorrência: **2/10**
- `recorder_thread` + asyncio main loop + novos loops por callback = 3 modelos de concorrência misturados.
- Não há lock no `client_websocket`. Em transição (close/reopen) tem janela.
- O `recorder.text()` é bloqueante — não cooperativo com asyncio.

### Uso de memória
- `fullSentences[]` cresce sem limite no browser (cada hora de meeting = ~200-500 frases).
- Server-side: apenas log no console, sem buffer de transcripts; ok.
- Imagens de screenshot não são removidas do disco (`screenshot.jpg` é overwritten, mas se a captura falhar em meio, sobra a anterior).

### Streaming realtime: **3/10**
- Transcrição realtime (token-by-token) está **desativada** (`enable_realtime_transcription=False`). O usuário só vê a frase quando o VAD fecha.
- Resposta do LLM **não é streamed** (`stream=False`). Espera a resposta inteira.
- Ambas as decisões são quick wins evidentes.

### Sincronização de UI: **5/10**
- Transcript prepend funciona, mas em sessões longas o `<div>` cresce indefinidamente no DOM (perfomance degrada após ~1000 nós).
- Sem indicação visual de "estou processando sua pergunta".
- Sem highlight da frase clicada (qual frase originou a resposta atual?).
- Sem auto-scroll inteligente (a sidebar de respostas pode ficar cortada).

### Tratamento de erros: **2/10**
- O log mostra que erros HTTP 400 da Groq vazam pelo `connection handler` da `websockets` e quebram a conexão. **Não há try/except** em torno das chamadas LLM.
- Se `setup_data.json` estiver mal-formado, `load_setup_data` levanta sem catch.
- Cliente não trata `socket.onerror` ou `socket.onclose`.

### Reconexão: **1/10**
- Browser v5 **não reconecta** (a v1 tinha; foi removido).
- Server **não detecta** quando o websocket morre — `client_websocket` continua apontando para o socket fechado, e a próxima `send` falha silenciosamente no `await`.

### Debounce / throttling
- Inexistente. Se o usuário clica em 5 botões em sequência, dispara 5 chamadas paralelas ao Groq. As respostas chegam fora de ordem e a UI não correlaciona resposta com pergunta.

### Gerenciamento de contexto longo: **1/10**
- Não existe. Cada clique é stateless.
- O usuário "recompõe" contexto manualmente clicando em "últimas N frases". Carga cognitiva alta no meio de uma reunião.

### Performance da transcrição
- `large-v2` é o modelo mais pesado da família "large" anterior. **Em CPU**, frases de 5 s tomam ~1-3 s para transcrever. Se você não tem GPU CUDA habilitada, esse é seu maior gargalo.
- Modelo `tiny.en` poderia rodar em paralelo para feedback instantâneo (texto "voador" enquanto `large-v2` finaliza), mas isso requer `enable_realtime_transcription=True`.

### Performance do frontend: **6/10**
- Vanilla JS rápido para o tamanho atual.
- Manipulação direta do DOM com `insertBefore` é eficiente para 50-100 nós; **degrada após 500+**.
- Sem virtualização da lista.

### Segurança: **1/10**
- API key Groq em texto plano, no git.
- Sem TLS (`ws://` não `wss://`).
- Sem autenticação no WebSocket.
- `bind` em `localhost` mitiga acesso de fora, mas se o usuário roda em outra máquina ou expõe pela rede, está aberto.
- `setup.php` + `save_setup.php` aceitam qualquer JSON sem validação — XSS/injection trivial se o conteúdo for renderizado em HTML algum dia.
- Screenshot do desktop inteiro do usuário enviado para um terceiro (Groq) — vazamento potencial de dados sensíveis na tela.

### Separação de responsabilidades: **2/10**
`server.py` mistura: (a) protocolo WS, (b) captura de áudio, (c) STT, (d) LLM bridge, (e) screenshot, (f) loading de configs, (g) prompts. Tudo em um único módulo de ~230 linhas.

### Qualidade do código
- **Code smells:** globals onipresentes, magic numbers (`8001`, `bbox`, `MAX_TOKENS=8000` no v3), strings de prompt embutidas, `try/except` sem log estruturado.
- **Anti-patterns:** event-loop-per-event, single-global-websocket, mixing threading + asyncio, dead code, hardcoded credentials.
- **Tech debt:**
  - Diferentes versões coexistindo (`example_browserclient` 1..5).
  - `start_server.bat` referenciando paths errados.
  - `socket.io.min.js` referenciado mas não usado.
  - Callback ligado a feature desligada.
- **Race conditions possíveis:**
  - Reconexão de cliente durante envio de `fullSentence`.
  - Clique em vários botões antes da primeira resposta chegar.
  - PHP regravando `setup_data.json` enquanto Python lê (race file-system; janela curta mas existe).
- **Memory leaks possíveis:**
  - `fullSentences[]` no browser (sem TTL).
  - Loops asyncio órfãos criados nos callbacks (`new_event_loop()` que nunca é fechado explicitamente).
- **Gargalos realtime:**
  - Whisper `large-v2` em CPU.
  - `stream=False` no LLM.
  - VAD `post_speech_silence_duration=0.1s` é agressivo demais → sentences fragmentadas.

---

## 5. Evoluções do Projeto Original (`KoljaB/RealtimeSTT`)

Desde Aug 2024 (data do fork) o upstream ganhou:

| Mudança | Por que importa para você |
|---|---|
| **`RealtimeSTT_server/`** com **dois WebSockets** (control + data) | Padrão "OpenAI Realtime"-like; isola comandos de stream. Vale incorporar. |
| **`SafePipe`** (substituto thread-safe de `mp.Pipe`) | Resolve bugs de IPC em multi-process; não muda muito sua arq atual. |
| **`normalize_audio`** (target -0.95 dBFS) | Melhora robustez em volumes baixos típicos de mic embutido. |
| **Callbacks assíncronos via threads-helpers** | Elimina exatamente o anti-pattern `asyncio.new_event_loop().run_until_complete(...)` que você usa. |
| **`wakeword_backend`** + **`faster_whisper_vad_filter`** | VAD interno do faster-whisper como filtro adicional — reduz alucinações em silêncio. |
| **VAD-aware pause de realtime transcription** | Pausa o modelo realtime quando detecta silêncio. Reduz custo CPU/GPU ao ficar em idle. |
| **WS-based server check** mais preciso | Reconexão mais robusta no client. |
| **`openai_voice_interface.py` modernizado** | Exemplo atualizado com novo SDK OpenAI + EdgeEngine TTS + shutdown gracioso. |
| **Suporte a `large-v3` / `large-v3-turbo`** (faster-whisper) | Modelo "turbo" tem qualidade próxima de `large-v2` com 2-4× a velocidade. **Quick win imediato.** |
| **`cpu_threads` / `num_workers` expostos** | Tuning fino para macOS/Apple Silicon e CPUs multi-core. |

> **Projetos irmãos relevantes:** `KoljaB/RealtimeVoiceChat` mostra um pipeline end-to-end conversacional (STT → LLM → TTS streaming). Embora você não queira TTS na mesa de reunião, **a estrutura modular dele é uma referência muito boa** para sua próxima arquitetura.

### O que vale incorporar
1. **`large-v3-turbo`** — substituir `large-v2` por `faster-whisper-large-v3-turbo` ganha 2-4× em latência sem perder qualidade.
2. **`faster_whisper_vad_filter=True`** — reduz inserções alucinadas em silêncio.
3. **`normalize_audio=True`** — robustez contra mics fracos.
4. **`RealtimeSTT_server`** como referência de protocolo (dual WS).
5. **Padrão de callback assíncrono** que o upstream usa (helper threads internas).

### O que NÃO vale incorporar (agora)
- Arquitetura multi-process do `RealtimeSTT_server` completa — overkill para single-user.
- Wake-word — sua UI baseada em clique resolve melhor a intenção do usuário.

### Conflitos com sua customização
- O `RealtimeSTT_server` upstream usa **protocolo binário de comandos** que diverge do seu JSON multiplexed. Adotar 100% o padrão upstream quebra `client.js`. **Solução:** adote o **modelo conceitual** (control + data WS), mas mantenha JSON.
- Os exemplos novos do upstream **capturam áudio no browser**. Se você adotar isso, precisa restaurar `getUserMedia` no `client.js` e mudar `use_microphone=True` para `False` + reativar `feed_audio`.

---

## 6. Melhorias Recomendadas

### 🔴 Alta prioridade (impacto imediato, baixa complexidade)

| # | Melhoria | Problema atual | Solução proposta | Dificuldade | Risco | Benefício esperado |
|---|---|---|---|---|---|---|
| H1 | **Streaming de resposta LLM** | `stream=False` faz o usuário esperar 1-3s pela resposta completa | Trocar para `stream=True` no Groq + emitir chunks `{type:'groqResponseDelta', delta}` no WS; renderizar token-a-token | Baixa | Baixo | -60% latência percebida |
| H2 | **Mover API key para `.env`** | Key Groq em git, em texto puro | `python-dotenv` + `.gitignore` + rotacionar a key | Trivial | **Crítico se exposta** | Elimina exposure |
| H3 | **Try/except em torno das chamadas LLM** | `BadRequestError` derruba a connection handler inteira (visível no log) | Wrapper com fallback de modelo + envio de `{type:'error', message}` | Trivial | Baixo | Resiliência operacional |
| H4 | **Trocar `large-v2` por `large-v3-turbo`** | Latência alta de transcrição | `'model': 'large-v3-turbo'` (faster-whisper) | Trivial | Baixo | -50-75% latência STT |
| H5 | **Reconexão no cliente v5** | Cair WS = sessão morre, refresh perde transcript | Reaproveitar `connectToServer()` + `setInterval` da v1; persistir `fullSentences` em `sessionStorage` | Baixa | Baixo | UX muito melhor em sessões longas |
| H6 | **Debounce de cliques + correlação de resposta** | Múltiplos cliques desordenam respostas | `requestId` em cada `groq` request, correlacionar no client; desabilitar botão até resposta chegar | Baixa | Baixo | Elimina confusão |
| H7 | **Highlight da frase clicada** | Sem feedback visual de qual frase originou a resposta | CSS class `.selected` na frase clicada, link visual com a resposta correspondente | Baixa | Nenhum | UX muito melhor |
| H8 | **Indicador de "thinking" / loading state** | Sem feedback de que o servidor recebeu | Spinner inline na resposta enquanto stream chega | Baixa | Nenhum | Reduz cliques duplicados |
| H9 | **Corrigir `start_server.bat`** | Aponta para diretório errado | `cd example_browserclient5` + URL correta | Trivial | Nenhum | Deixa de quebrar onboarding |

### 🟡 Média prioridade (estruturais, valem o esforço)

| # | Melhoria | Problema atual | Solução proposta | Dificuldade | Risco | Benefício esperado |
|---|---|---|---|---|---|---|
| M1 | **Substituir `websockets` low-level por FastAPI + WS + SSE** | Servir setup, healthchecks, métricas, e WS no mesmo processo é hoje impossível | `FastAPI` com endpoint `/ws` + `/setup` + estáticos | Média | Médio | Unifica stack, abre porta para extensões |
| M2 | **Eliminar `asyncio.new_event_loop()` em callbacks** | Anti-pattern + leaks potenciais | Usar `asyncio.run_coroutine_threadsafe(..., loop)` com loop principal injetado na thread | Média | Médio (testes) | Estabilidade |
| M3 | **Connection manager multi-cliente** | Single global; abrir 2 abas quebra | Set de conexões; broadcast de transcripts; correlação por `client_id` | Média | Baixo | Permite operador + observador |
| M4 | **Memória conversacional opcional** | v3 tinha, v5 perdeu | Sliding window de últimas K frases + últimas R respostas como contexto opt-in via toggle UI | Média | Baixo | Respostas mais coerentes em rodadas longas |
| M5 | **Persistir sessão (transcripts + respostas)** | Perdido em refresh | SQLite local com `sessions` table; export JSON/MD | Média | Baixo | Reaproveitamento pós-reunião |
| M6 | **Realtime transcription incremental opcional** | UI fica em silêncio até VAD fechar | Ativar `enable_realtime_transcription=True` com `tiny.en`; renderizar com fade-out até frase final | Média | Baixo | UX "viva" |
| M7 | **Validação e schema de prompt** | Strings concat no `server.py` | `Pydantic` models + templates (Jinja2) externos | Média | Baixo | Manutenibilidade |
| M8 | **Selector de persona na UI** | Hoje tem 2 JSONs (`setup_data.json` e `setup_data_job.json`) sem switch | Lista presets + dropdown | Baixa | Baixo | Workflow mais ágil |
| M9 | **Logging estruturado** | `print()` por todo lado | `loguru` ou `structlog` + nível configurável | Baixa | Nenhum | Debug realtime |
| M10 | **Configurar bbox de screenshot dinamicamente** | Hardcoded `(0,140,2200,1820)` quebra em outros monitores | Detectar resolução com `pyautogui.size()` ou seletor de janela | Baixa | Baixo | Portabilidade |

### 🟢 Baixa prioridade (futuro, evoluções)

| # | Melhoria | Solução proposta | Benefício |
|---|---|---|---|
| L1 | Captura de áudio no browser (multi-device) | Restaurar `getUserMedia`, manter server-side como fallback | Permite uso remoto |
| L2 | TLS (`wss://`) + auth simples (token) | Reverse proxy (Caddy/Traefik) + token query param | Permite expor à internet |
| L3 | Multi-LLM | Adapter pattern para Groq / OpenAI / Anthropic / local | Resiliência + custo |
| L4 | Detecção de quem fala (diarização) | `pyannote` ou `whisperx` | "Ele disse X, eu disse Y" |
| L5 | Tradução em tempo real (PT ↔ EN) | Pipeline paralelo com `argos-translate` ou Groq | Para cenários onde EN é difícil |
| L6 | Modo "ditado privado" | Toggle que desliga LLM e só transcreve para Markdown | Notas pessoais |
| L7 | Plugins / actions configuráveis | "Action registry" — cada botão é um YAML com prompt + modelo | Personalização sem código |
| L8 | Atalhos de teclado | `1..8` envia últimas N, `Esc` cancela, `R` repete | Operadores avançados |
| L9 | Métricas / observabilidade | Prometheus + dashboard local | Tuning de latência |
| L10 | Empacotar como app desktop (Tauri/Electron) | Single-binary, sem WAMP | Distribuição |

---

## 7. Arquitetura Recomendada (Nova Versão)

```
┌─────────────────────────── DESKTOP CLIENT (browser ou Tauri) ───────────────────────┐
│                                                                                      │
│  React/Svelte SPA                                                                    │
│  ├── TranscriptStream  (zustand store, virtualized list)                             │
│  ├── ChatPanel         (streaming markdown, request correlation by id)              │
│  ├── ActionBar         (presets de N + actions plugáveis)                            │
│  ├── PersonaSelector   (dropdown de presets)                                         │
│  ├── Highlights        (frase ↔ resposta linkados visualmente)                       │
│  └── Realtime Indicators (VAD ON, STT thinking, LLM streaming)                       │
│                                                                                      │
│  Conexões:                                                                           │
│  ├── /ws/data    (transcript stream + audio se browser-side)                         │
│  └── /ws/control (commands: action.invoke, persona.select, session.export)           │
│                                                                                      │
└──────────────┬──────────────────────────┬────────────────────────────────────────────┘
               │ wss + token              │
┌──────────────▼──────────────────────────▼────────────────────────────────────────────┐
│                          FastAPI Gateway (uvicorn, asyncio)                          │
│                                                                                      │
│  ┌────────────────────┐   ┌────────────────────┐   ┌──────────────────────────────┐  │
│  │ /ws/data handler   │   │ /ws/control handler│   │ /api/persona, /api/sessions  │  │
│  └─────────┬──────────┘   └────────┬───────────┘   └──────────────┬───────────────┘  │
│            │                       │                              │                  │
│  ┌─────────▼───────────────────────▼──────────────────────────────▼───────────────┐  │
│  │                          EVENT BUS (asyncio.Queue / Redis)                     │  │
│  │  events: audio.chunk, transcript.partial, transcript.final, action.invoke,     │  │
│  │          llm.delta, llm.final, error.*                                         │  │
│  └─────┬─────────────┬─────────────────────┬─────────────────────┬────────────────┘  │
│        │             │                     │                     │                    │
│   ┌────▼────┐  ┌─────▼──────┐  ┌──────────▼─────────┐  ┌────────▼────────┐           │
│   │STTWorker│  │ContextOrch │  │ActionRegistry      │  │SessionStore     │           │
│   │(thread) │  │(asyncio)   │  │(plugins/YAML)      │  │(sqlite/litestream)│         │
│   │RealtimeS│  │window+rag  │  │groq/openai/anthr.  │  │                 │           │
│   │TT v3-tur│  │persona     │  │vision adapter      │  │                 │           │
│   └─────────┘  └────────────┘  └────────────────────┘  └─────────────────┘           │
│                                                                                      │
│  Observability: structlog → file + OpenTelemetry → local Tempo (opcional)            │
└──────────────────────────────────────────────────────────────────────────────────────┘

           ┌─────────────────────── External LLMs ────────────────────────┐
           │  Groq (primary)     OpenAI (fallback)    Anthropic (premium) │
           └──────────────────────────────────────────────────────────────┘
```

### Princípios da nova arquitetura

1. **Event-driven, não call-chain.** Toda a transição entre captura → STT → orquestração → LLM passa por um event bus. Permite plug-in (multi-action), substituição de motores, e observabilidade.
2. **Modularização rígida** em "workers": STTWorker, ContextOrchestrator, ActionRegistry, SessionStore, LLMAdapter. Single-responsibility.
3. **Context Orchestrator** com sliding window + persona + (opcional) RAG sobre additional_info quando ele virar grande (ex.: knowledge base de produto).
4. **Action Registry plugável.** Cada botão (ou shortcut) é uma `Action(yaml)`:
   ```yaml
   id: respond-as-me
   trigger: click | shortcut | auto
   prompt_template: ./prompts/respond_as_me.j2
   model: groq:llama-3.3-70b-versatile
   stream: true
   include_context: { last_n_sentences: 5, include_persona: true }
   ```
5. **Streaming end-to-end:** STT → cliente (parciais) e LLM → cliente (deltas). UI mostra ambos em fluxo.
6. **Persistência:** SQLite local + WAL para sessões; export `.md` ao final.
7. **Fila + retry + dead-letter** entre worker e LLM para tolerar falhas transitórias (ex.: model decommissioned).
8. **Observabilidade:** logs estruturados, métricas (latência STT/LLM/UI), traces de cada request com `requestId`.
9. **Escalabilidade horizontal opcional:** trocando `asyncio.Queue` por Redis e isolando STTWorker em outro processo, dá para servir múltiplos usuários no mesmo servidor (ex.: equipe de vendas).
10. **Tecnologias sugeridas:** FastAPI, Pydantic v2, faster-whisper (large-v3-turbo), RealtimeSTT (manter), Groq SDK + OpenAI SDK como adapters, Jinja2 para prompts, SQLAlchemy + SQLite, structlog, React + Tailwind + Zustand (ou Svelte 5 — leve), Tauri se quiser desktop nativo.

---

## 8. Melhorias de UX/UI

- **Diferenciação visual transcript vs respostas:** já existe (transcript main + sidebar). Reforçar com **borda/badge "you said" vs "they said"** se diarização entrar.
- **Timeline inteligente:** linha do tempo vertical com timestamps de cada frase. Útil para retomar contexto após uma pausa.
- **Snippets contextuais:** ao passar o mouse em uma resposta, destacar **a frase de origem** no transcript.
- **Seleção de trecho livre:** permitir clicar e arrastar para selecionar várias frases, não só "últimas N". Mais natural para "o cliente acabou de fazer 3 perguntas seguidas".
- **Feedback visual de streaming:** caret piscando no fim do texto enquanto o LLM streama; barra de progresso de VAD enquanto fala.
- **Loading states explícitos:** "STT pensando" (frase em itálico, fade), "LLM pensando" (spinner antes do primeiro token).
- **Indicadores de confiança:** se o STT retornar `avg_logprob` ou `no_speech_prob`, marcar trechos com baixa confiança em opacidade reduzida — sinal para o usuário hesitar antes de clicar.
- **Atalhos de teclado:** `1..8` = últimas N frases (já no botão); `Space` = transcrever / pausar; `R` = repetir última pergunta; `Esc` = cancelar request em vôo; `S` = trocar persona.
- **UX para operador humano (você ao vivo):** texto **grande**, contraste alto (já é black-on-yellow/cyan), estimativa de tempo de leitura (ex.: "~12 s para falar"), modo "respostas curtas" (cap em N tokens).
- **Modo "panic":** botão grande "REPHRASE SHORTER" que pega a última resposta e força reescrita em ≤2 frases.
- **Histórico filtrável:** após a reunião, página `/sessions/<id>` com transcript + respostas + tags, exportável em Markdown.
- **Persona switching mid-meeting:** a reunião pode mudar de tom (ex.: pitching → detalhes técnicos) — dropdown sempre visível.
- **Indicador de "internet/Groq saudável":** se a Groq cair, banner discreto.
- **Anti-distração:** modo "minimal" que só mostra a resposta atual, escondendo transcript completo.

---

## 9. Roadmap de Refatoração

### Fase 0 — Quick wins de baixo risco (0,5 a 1 dia)
- Mover key Groq para `.env`, rotacionar a anterior.
- Trocar `large-v2` → `large-v3-turbo`.
- `stream=True` no Groq + render token-a-token.
- Try/except em `handle_groq_request` e `handle_screenshot_request`.
- Reconexão no cliente v5 (resgatar da v1).
- Corrigir `start_server.bat`.
- Remover `socket.io.min.js`, código morto de demux, callback órfão.

### Fase 1 — Estabilização (2 a 4 dias)
- Substituir `asyncio.new_event_loop()` por `asyncio.run_coroutine_threadsafe(..., main_loop)`.
- Connection manager (set de WS).
- `requestId` + correlação cliente↔servidor.
- Logging estruturado (`structlog`).
- Highlight da frase clicada + estado "thinking" na UI.

### Fase 2 — FastAPI + persistência (3 a 6 dias)
- Migrar para FastAPI mantendo JSON wire-format atual (compatibilidade do `client.js`).
- `/setup` nativo Python (deprecar PHP).
- SQLite para sessões.
- Selector de persona na UI.

### Fase 3 — Event bus + Action Registry (1 a 2 semanas)
- Refatorar para arquitetura event-driven interna.
- Action Registry em YAML.
- Memória conversacional opcional.

### Fase 4 — Frontend moderno (1 a 2 semanas)
- Reescrever `client.js` em React/Svelte com lista virtualizada.
- Snippets, seleção livre, atalhos.
- Modo "panic".

### Fase 5 — Distribuição (variável)
- TLS + auth.
- Tauri/Electron para desktop.
- Multi-LLM adapter.

### Estratégia de rollout
- **Branch por fase** (`refactor/phase-0`, `refactor/phase-1`, ...).
- Cada fase mantém compatibilidade do **wire format** com a UI antiga até a Fase 4.
- **Feature flags** simples (env var) para alternar entre `large-v2`/`large-v3-turbo`, `stream true/false`, etc., para A/B em reuniões reais.

### Estratégia de testes
- **Pytest** para o backend, com `pytest-asyncio`.
- **Recordings em arquivo:** salvar 5-10 conversas reais em WAV + transcript ground truth → suite de regressão para STT.
- **Snapshot tests** para os prompts (ver mudanças no prompt como diff).
- **Smoke test E2E** com `playwright`: abre browser, manda audio gravado, valida que a resposta chega.

---

## 10. Próximos Passos Práticos

### Primeiras 5 mudanças (em ordem)
1. **Rotacionar a key Groq** (a atual no repo está comprometida) e movê-la para `.env`. **Antes** de qualquer outro commit.
2. **Trocar `model: 'large-v2'` para `'large-v3-turbo'`** em `server.py:38` (medir antes/depois com o mesmo áudio).
3. **`stream=True`** nas duas chamadas Groq (`server.py:213`, `server.py:176`) + emitir `groqResponseDelta` no WS + renderizar incremental.
4. **Try/except** envolvendo as chamadas LLM com fallback de modelo configurável.
5. **Corrigir `start_server.bat`** e remover código morto (demux binário, callback órfão, `socket.io.min.js`).

### Arquivos principais a revisar
- `example_browserclient5/server.py` — o coração; merece ser quebrado em 4-5 módulos.
- `example_browserclient5/client.js` — limpar e modernizar (mesmo antes de migrar para React, dá para introduzir state proper e reconexão).
- `example_browserclient5/setup.php` + `save_setup.php` — substituir por endpoint Python.
- `example_browserclient5/start_server.bat` — corrigir paths.
- `RealtimeSTT/audio_recorder.py` (upstream) — verificar se há config nova relevante (`normalize_audio`, `faster_whisper_vad_filter`).

### Módulos mais críticos (em ordem de risco operacional)
1. **`recorder_thread`** — bloqueante, sem shutdown gracioso.
2. **`echo`** (handler WS) — sem error boundary, derruba a sessão em qualquer erro de modelo.
3. **`handle_groq_request`** — sem retry, sem timeout, sem streaming.
4. **`load_setup_data`** — re-leitura de disco a cada chamada, sem cache.

### Sequência ideal de refatoração
**Semana 1:** Fase 0 (quick wins) + parte da Fase 1 (try/except + reconexão).
**Semana 2-3:** Restante da Fase 1 + Fase 2 (FastAPI + SQLite).
**Semana 4-5:** Fase 3 (event bus + actions YAML).
**Semana 6+:** Fase 4 (UI moderna) e Fase 5 sob demanda.

### Riscos imediatos
- Key Groq exposta no git (impacto financeiro + abuso).
- `connection handler failed` toda vez que um modelo é decommissioned (impacto de reunião travada).
- `start_server.bat` quebrado (impacto de onboarding e setup em outra máquina).
- `screenshot.jpg` no diretório do projeto pode incluir dados sensíveis e ser comitado por engano.

---

## 📋 Resumo Executivo

A aplicação `example_browserclient5` é um **protótipo funcional e diferenciado** de copilot de reunião que ataca um problema real e específico (responder em inglês como o próprio usuário, em tempo real). A **UX é o ativo principal** — a combinação clique-na-frase + presets-N + persona configurável + screenshot-vision é genuinamente útil e difícil de encontrar pronto.

A **engenharia, porém, é frágil**: protótipo de single-file, single-client, single-event-loop misturado com threading, com uma série de anti-patterns (event loop por callback, key API hardcoded, código morto, callbacks órfãos, sem reconexão, sem streaming) que limitam a usabilidade em sessões reais e travam a evolução do produto.

A boa notícia é que **pouca coisa precisa ser jogada fora**. Cinco quick wins (key em .env, modelo turbo, streaming LLM, try/except, reconexão) já entregam ~70% da melhoria perceptível pelo usuário em menos de uma semana. A reescrita estrutural (FastAPI + event bus + Action Registry + UI moderna) é incremental, com compatibilidade preservada, e vale ser feita em fases ao longo de 4-6 semanas.

A direção estratégica é clara: **transformar o "copilot" em uma plataforma de "actions" plugáveis** — a frase clicada é só uma das ações; futuramente "explicar termo técnico", "rephrase shorter", "generate follow-up question", "translate to PT" são outras actions YAML. Esse design dá longevidade ao produto.

---

## 🔥 Top 10 Maiores Problemas

1. **API key Groq hardcoded em texto plano no git** (`server.py:18`) — risco de segurança e financeiro.
2. **`stream=False` no LLM** — usuário espera 1-3 s pela resposta inteira, inviável ao vivo.
3. **`asyncio.new_event_loop().run_until_complete()` chamado a cada callback** — anti-pattern grave, leaks potenciais, instabilidade no Windows.
4. **Single global `client_websocket`** — abrir 2 abas quebra; desconexão silenciosa.
5. **Sem try/except em torno das chamadas Groq** — `BadRequestError` derruba o connection handler inteiro (já visto no log).
6. **`large-v2` em CPU** quando `large-v3-turbo` daria 2-4× a velocidade na mesma qualidade.
7. **Sem reconexão no client v5** (a v1 tinha) — sessão morre se WS cair.
8. **Sem memória conversacional** entre cliques — cada resposta é stateless, perde-se coerência em sequências.
9. **`start_server.bat` aponta para diretórios errados** — onboarding quebrado.
10. **`bbox` de screenshot hardcoded** para o monitor específico do Evaldo — não-portátil.

## 🚀 Top 10 Melhorias de Maior Impacto

1. **Streaming de tokens LLM** (Groq `stream=True`) → -60% de latência percebida.
2. **`large-v3-turbo`** → -50-75% de latência STT.
3. **Try/except + retry com fallback de modelo** → operação robusta.
4. **Streaming incremental de transcrição** (`enable_realtime_transcription=True` com `tiny.en`) → UX "viva".
5. **Reconexão automática + persistência da sessão (sessionStorage + SQLite)** → sessões longas viáveis.
6. **`requestId` + correlação cliente↔servidor + highlight da frase clicada** → elimina confusão multi-clique.
7. **Action Registry em YAML** (rephrase, follow-up, translate, explain) → o copilot vira plataforma.
8. **Memória conversacional opcional** (sliding window) → respostas coerentes em rodadas longas.
9. **Selector de persona na UI + presets** → reduz fricção de troca de contexto (networking → entrevista → demo técnica).
10. **FastAPI + connection manager + logging estruturado** → base sólida para todo o resto.

---

## 🏛️ Proposta Resumida de Arquitetura Futura Ideal

> Um **Cowork-like de reuniões pessoais**: event-driven, plugável, observável, single-binary distribuível.

- **Captura:** server-side (mic local, default) com fallback de captura no browser (`getUserMedia`) para uso remoto.
- **STT:** RealtimeSTT (faster-whisper `large-v3-turbo`) em thread isolada, com `normalize_audio` e `faster_whisper_vad_filter`. Stream **incremental** de partials + finals.
- **Bus interno:** `asyncio.Queue` (single-process) com upgrade-path para Redis (multi-user).
- **Context Orchestrator:** sliding window de últimas K frases + R respostas + persona + (opcional) RAG sobre `additional_info` grande.
- **Action Registry:** cada ação é um YAML (template + modelo + parâmetros + condições de inclusão de contexto). UI lê o registry e renderiza botões/atalhos automaticamente.
- **LLM Adapter:** Groq (primary), OpenAI/Anthropic como fallback ou premium. Retry exponencial + circuit breaker.
- **Persistência:** SQLite local com WAL para sessões; export `.md`/`.json`.
- **Frontend:** React/Svelte SPA com lista virtualizada, streaming markdown, atalhos, snippet linking, modos "panic"/"minimal".
- **Servidor:** FastAPI (uvicorn) com `/ws/data`, `/ws/control`, REST para setup/sessions, estáticos. TLS opcional via reverse proxy.
- **Observabilidade:** `structlog` + métricas Prometheus locais (latências STT/LLM/UI) + `requestId` em todos os logs.
- **Distribuição:** Tauri (single-binary cross-platform) ou Electron, com Python embarcado. WAMP some.

---

## Sources

- [GitHub - KoljaB/RealtimeSTT](https://github.com/KoljaB/RealtimeSTT)
- [Releases · KoljaB/RealtimeSTT](https://github.com/KoljaB/RealtimeSTT/releases)
- [RealtimeSTT_server/README.md](https://github.com/KoljaB/RealtimeSTT/blob/master/RealtimeSTT_server/README.md)
- [audio_recorder.py upstream](https://github.com/KoljaB/RealtimeSTT/blob/master/RealtimeSTT/audio_recorder.py)
- [RealtimeVoiceChat (projeto irmão de referência)](https://github.com/KoljaB/RealtimeVoiceChat)
- [Faster-Whisper vs GGML — discussão #222](https://github.com/KoljaB/RealtimeSTT/discussions/222)
