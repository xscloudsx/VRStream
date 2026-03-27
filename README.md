# VRStream
**Desenvolvido e Criado por:** Kauã

VRStream é um motor de transmissão independente RTMP -> HLS de ultrabaixa latência. Construído com FastAPI e FFmpeg, foi desenhado para aguentar transmissões de vídeo extremas para o VRChat e WebPlayers, rodando sob servidores Linux com máxima prioridade de processamento.

## 🚀 Melhorias e Otimizações Recentes (Turbo Upgrade)
Este repositório foi altamente modificado e elevado ao nível "produção militar" para transmissão ao vivo estável:

* **🚀 Gravação em RAM Bypass (Tmpfs):** No ambiente Linux, o sistema detecta e salva os segmentos brutos de vídeo de tela (`.ts`) diretamente na Memória RAM Virtual (`/dev/shm`), reduzindo o delay de leitura/escrita de HDs ou SSDs para **ZERO**.
* **🎮 Tela de Descanso Offline Automática:** Se o seu OBS cair ou você decidir desligar o motor pelo painel principal, o sistema ativa um Sub-Processo FFmpeg que inicia nativamente uma live paralela em Loop do arquivo `Offline.mp4`. Isso preserva o sinal do VRChat blindando contra congelamentos! Tudo de forma gapless (sem emendas).
* **⚡ CBR Hard-Limit e FastDecode:** O pipeline da API foi cimentado com restrições máximas. Transição de VBR/CRF para CBR Estrito com `bufsize` duplicado para estabilidade. Forçamos o `zerolatency` e `fastdecode` para decapar 100% de efeitos visuais pesados na CPU do seu servidor.
* **🎥 Constant Frame Rate e Corte Matemático:** Operação estritamente amarrada em **30 FPS**. Cimentada usando `-g 30`, `-keyint_min 30` e limite de Threshold para gerar segmentos com precisão microscópica. Nada sai do compasso. 
* **🕵🏻‍♂️ Rastreamento de Ping Anônimo:** A latência (ms) exata com que cada espectador da Web está sofrendo o HLS é interceptada e enviada dinamicamente a cada 3 segundos ao painel do streamer. Todos os IP's são brutalmente convertidos para Hashes MD5 irreversíveis, preservando a segurança de dados e privacidade da comunidade.
* **🧹 Log de Segmentos e Painel Refatorado:** Painéis visuais ricos adicionados no FrontEnd local com sistema de filtragem `[NEW]`, `[ERROR]`, `[WARN]`, `[WIPED]` que lê a rastro de lixeira e erros de rede sem engasgar o Front.
* **🛡️ Lixo Limpado Automaticamente na RAM:** Varredura autônoma Async de lixo digital (qualquer arquivo `.ts` estancado que passar de 20s de vida é removido atomicamente, mantendo o consumo de RAM estático e inquebrável).
* **💥 Prioridade Endêmica Linux SystemD:** O Instalador Ubuntu foi reformulado para escalar permissões brutas no sistema hospedeiro. Adicionados `Nice=-10` (Alta permissão de CPU de Kernel sob todo o restante do SO) e `OOMScoreAdjust=-500` (Blindando o processo contra queda por falta de memória do sistema) rodando nativamente como root para o deamon SystemCtl.

## ⚙️ Instalação no Linux (Ubuntu 24.04+)
1. Suba a pasta inteira para seu servidor ubuntu.
2. Rode `sudo bash install_ubuntu.sh`
3. Monitore com: `sudo systemctl status vrstream`

> **Nota:** Não inicie o `launcher.py` no Ubuntu Server visto que ele é desenhado para interfaces gráficas interativas do Windows! O Painel de Controle HLS web subirá automaticamente no IP do NodeJS na porta padrão (ou as dos Túneis Ngrok/Cloudflared integrados).