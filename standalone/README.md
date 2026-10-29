# Receptor independente para comparação com lorarx

Adaptação experimental para meshcoreSDR. Recebe **IQ CF32 por stdin**, sem GNU Radio,
sem depender do modelo de SDR e sem transmitir RF. O projeto original permanece
nas outras pastas; não alteramos seus blocos.

## O que é derivado da EPFL

Base: `tapparelj/gr-lora_sdr`, revisão `862746dd1cf635c9c8a4bfbaa2c3a0ec3a5306c9`.
O decoder adapta o mapeamento Gray, o desentrelaçamento, a escolha por máxima
verossimilhança das 16 palavras Hamming, o cabeçalho e CRC. Usa as energias dos
256 bins para métricas max-log por bit (alternativa simplificada por energia,
não o cálculo completo de verossimilhança/Bessel da EPFL). FFT KISS e tabela de
whitening são reaproveitadas do upstream. Atribuição: Joachim Tapparel e demais
autores EPFL indicados no projeto original. Código derivado sob GPL-3.0-or-later;
KISS FFT preserva a atribuição BSD nos fontes.

**Não é uma extração equivalente de toda a cadeia EPFL**. Sincronização simplificada,
NCO e reamostrador são próprios. Ainda não há o estimador fracionário de STO,
o acompanhamento de SFO ou toda a estratégia de sincronização do upstream.
O teste mede este receptor derivado, não garante reproduzir a sensibilidade
publicada pela EPFL. Também não isola apenas a diferença de FEC.

## Entrada comum para RSP1 e RTL-SDR

CF32 little endian: I e Q float32 intercalados, 8 bytes por amostra complexa.
O decoder recebe taxa real, centro do IQ e frequência do canal, independentemente
se a origem é SDRplay, RTL-SDR ou arquivo sintético.

| Origem | Taxa de entrada usada | Centro | Canal |
|---|---:|---:|---:|
| RSP1 atual | **500000 S/s** | 923250000 Hz | 923125000 Hz |
| RTL-SDR, configuração anterior | **1024000 S/s** | 923250000 Hz | 923125000 Hz |

O front-end próprio desloca o canal, filtra e reamostra para **250000 S/s internos**
(4×BW). Isso não altera a taxa do hardware. O lorarx recebe o IQ original, na taxa
original. A sincronização reduz a resolução usada pela FFT para 256 pontos/símbolo.
FIR de interpolação: 64 taps, janela Hann, corte de 40 kHz; sua rejeição de
interferências e custo na placa ainda precisam de avaliação com RF real.

Configuração inicial fixa: SF8, BW62500 Hz, sync 0x12, cabeçalho explícito e CRC
presente. CR vem do cabeçalho; CR4/8 é o alvo da rede local. LDRO desligado:
duração do símbolo 4,096 ms. Não é decoder universal de todas as configurações LoRa.

## Compilar e conferir

```sh
cd standalone
make
make check
```

Requisitos: GCC/G++, Make; Python3 e NumPy apenas para os testes e captura Soapy.
Não requer GNU Radio, Boost ou FFTW para o executável standalone.

Os testes são sintéticos, sem abrir SDR. Exercitam CF32 nas taxas 500000 e 1024000,
offset de canal -125000 Hz, CFO +480 Hz, desalinhamento de início, preâmbulo
estendido, cabeçalho explícito, CRC correto e propositalmente errado e CR4/6,
4/7 e 4/8. Onde o lorarx local existe, testam também a distribuição do mesmo IQ
para ambos. Isso não comprova recepção fraca, multipercurso ou compatibilidade
com hardware em funcionamento. O conteúdo das fixtures é texto inventado;
nenhum pacote de usuário ou chave é publicado.

## Comparar o mesmo IQ, sem gravar IQ em disco

`compare.py` lê stdin e entrega os mesmos blocos CF32 aos dois processos, com
filas limitadas a 2 MiB cada. Encerra o teste se um decoder não acompanhar,
sem omitir amostras silenciosamente. O teste é independente dos serviços ativos.

```sh
python3 compare.py --sample-rate 500000 < entrada.cf32
```

A entrada deve corresponder ao centro/canal informados. Não abrir um segundo
capturador no mesmo SDR ocupado: a abertura concorrente pode falhar e invalidaria
a comparação. Os exemplos abaixo são para uma janela de teste em que o hardware
esteja livre; estes comandos não foram iniciados no sistema em produção.

RSP1 (500 kS/s; exige a API SDRplay e bindings Soapy do sistema):

```sh
set -o pipefail
python3 capture_iq.py --driver sdrplay --sample-rate 500000 --seconds 60 |
  python3 compare.py --sample-rate 500000
```

RTL-SDR Blog V4 (1.024 MS/s; exige a biblioteca Blog V4 isolada):

```sh
set -o pipefail
LD_LIBRARY_PATH=/opt/rtlsdr-blog-v4/lib python3 capture_iq.py \
  --driver rtlsdr --serial RTL-0 --sample-rate 1024000 --seconds 60 |
  python3 compare.py --sample-rate 1024000
```

Captura Soapy: AGC ativo, CF32, taxa confirmada contra a solicitada, watchdog de
15 segundos, teste abortado em erro de stream/gap. RTL bias tee explicitamente
OFF; nenhuma alteração de EEPROM. Respeita SIGINT/SIGTERM e fecha o stream.
Os drivers e escalas de amplitude podem diferir; o formato IQ comum não torna
RSSI/SNR entre dispositivos automaticamente calibrados.

## Logs e interpretação

Diretório padrão `/var/log/meshcoresdr/epfl-comparison`, coberto pelo log2ram
neste Tinker2. Precisa de permissão de escrita; para execução como meshcoresdr,
crie o diretório com esse proprietário. Há dois conjuntos de logs rotativos,
lorarx.jsonl e epfl.jsonl, cada um 2 MiB mais dois backups (~12 MiB total).
Somente registros JSON e metadados da comparação são gravados, não IQ bruto.
O log2ram pode sincronizar esses arquivos para armazenamento conforme sua política.

`received_at` é o horário UTC de saída do decoder, não timestamp RF de hardware.
`sample_index` é relativo ao fluxo interno de 250 kS/s e inclui o atraso do FIR;
não comparar como se fosse índice do fluxo original. Para parear resultados,
compare o payload em bytes (base64 no lorarx, hexadecimal no experimental),
CRC, janela de recepção e parâmetros. Tempo de processamento diferente pode
produzir horários de saída diferentes. O sync informado em JSON significa que
a detecção passou na tolerância de sincronização, não identidade de nó confirmada.
CRC válido confirma integridade PHY; ainda não houve interpretação/validação
MeshCore ou assinatura neste receptor.

Nada desta adaptação envia ao MQTT ou companion, instala serviços ou troca
`/usr/bin/lorarx`. O lorarx atual segue como decoder da captura de produção.

## Correção de interpretação do sync

O JSON upstream lorarx escreve net em decimal: net=18 representa 0x12, não 0x18. Esta adaptação usa 0x12 (chirps de sync 8 e 16). A afirmação anterior na conversa de que net=18 seria hexadecimal estava incorreta. O coletor meshcoreSDR já compara o inteiro decimal 18 e não precisou mudar.
