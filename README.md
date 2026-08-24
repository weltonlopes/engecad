# EngeCAD

CAD livre para **mapeamento e plantas cadastrais**: desenhe sobre ortofoto
georreferenciada, com sistema de coordenadas de verdade, precisão topográfica na
entrada por teclado e automação em Python.

Formato nativo **DXF** — o arquivo abre no AutoCAD e no QGIS sem exportar nada.

```
Versão 0.1.0 · licença GPL-3.0 · Python 3.10–3.13
```

---

## Instalação

O PySide6 ainda não publica wheels para o Python 3.14, então o venv precisa ser
criado explicitamente com o 3.13 (ou 3.12):

```bash
py -3.13 -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

Rodar:

```bash
python -m engecad
python -m engecad planta.dxf     # abre um desenho direto
```

---

## O que a v0.1 faz

| | |
|---|---|
| **Vista** | pan (botão do meio), zoom ancorado no cursor, grade adaptativa, escala 1:N |
| **Coordenadas** | CRS via PROJ/pyproj, SIRGAS 2000 / UTM pré-configurado, E/N ao vivo na barra de status |
| **Imagem de fundo** | GeoTIFF/COG, JP2, ECW (ver abaixo), reprojeção on-the-fly, leitura decimada por overviews |
| **Desenho** | `LINE`, `PLINE` com preview elástico e distância/azimute ao vivo |
| **Snap** | extremidade, ponto médio, centro, quadrante, interseção, próximo, grade |
| **Camadas** | visibilidade, cor ACI, camada corrente |
| **Medição** | `DIST` (distância + azimute em GMS), `AREA` (área + perímetro + hectares) |
| **Arquivo** | abrir/salvar DXF R2018 + sidecar `.emap.json` |
| **Automação** | console Python embutido (F9) e execução de arquivos `.py` |

### Entrada de coordenada — o que dá precisão topográfica

Com uma ferramenta ativa, digite na linha de comando:

```
500123.45,7412987.12    absoluto no CRS do projeto
@10,0                   relativo ao último ponto
@10<90                  polar CAD (anti-horário a partir do leste)
@10<<45                 por AZIMUTE (horário a partir do norte)
@100<<45d30'20"         azimute em grau/minuto/segundo
```

O `<<` para azimute é a forma direta de lançar um memorial descritivo.

---

## Sobre o ECW — leia antes de reclamar que não abre

ECW é formato proprietário da Hexagon. A leitura é gratuita no desktop, mas a
biblioteca **não é redistribuível** — é por isso que nem o QGIS a embute, e por
isso que o GDAL que vem nos wheels do `rasterio` não traz o driver.

Consequência: com um `pip install` puro não dá para ler **nem converter** ECW.
Precisa existir na máquina um GDAL com o driver.

O EngeCAD tenta, nesta ordem:

1. abrir direto com o GDAL embutido;
2. procurar um GDAL externo com ECW (`%ENGECAD_GDAL_BIN%`, `C:\OSGeo4W\bin`,
   `C:\Program Files\QGIS*\bin`) e oferecer **conversão para COG**, feita uma
   única vez — o COG depois navega mais rápido que o ECW original;
3. se não achar nada, explicar como instalar.

**Para habilitar ECW:** instale o [OSGeo4W](https://trac.osgeo.org/osgeo4w/) e
marque o pacote `gdal-ecw`. Depois reabra o EngeCAD.

Para saber o que a sua máquina suporta: **Ajuda › Diagnóstico de raster (ECW)**.

> O GDAL externo é chamado por *subprocess*, nunca importado. Carregar duas
> cópias de GDAL/PROJ no mesmo processo Python é causa clássica de crash
> silencioso no Windows.

---

## Console Python

`F9` abre o console. A API já está no escopo, sem import:

```python
add_line((500000, 7400000), (500050, 7400000))
add_polyline([(0,0), (10,0), (10,10)], closed=True, layer="LIMITE")
area([(0,0), (10,0), (10,10), (0,10)])       # 100.0
azimuth((0,0), (10,0))                        # 90.0
to_wgs84(500000, 7400000)                     # (-51.0, -23.5101947)
command("LINE")                               # aciona o mesmo comando da barra
zoom_extents()
```

**Cada execução vira um único item de desfazer.** Um script que cria 500
entidades some com um `Ctrl+Z`; um script que levanta exceção no meio não deixa
metade do trabalho no desenho. Veja `examples/exemplo_quadra.py`.

---

## Arquitetura — as três decisões que sustentam o resto

**1. Precisão: nenhuma coordenada UTM chega ao Qt.**
Uma coordenada UTM tem magnitude ~7,4×10⁶. Entregue crua ao rasterizador do Qt
(via `QTransform` ou `QGraphicsView`), ela é processada internamente em precisão
simples e o desenho passa a tremer na casa do meio metro. Por isso
`render/viewport.py` faz toda a transformação em `float64` no Python e entrega
ao painter apenas coordenadas de **tela**, que são números pequenos. É também a
razão de o canvas ser um `QWidget` próprio, e não `QGraphicsView`.

**2. Registro de comandos único.**
Linha de comando, console Python e (v0.4) AutoLISP despacham todos por
`core/registry.py`. Construir esse funil já com 4 comandos é o que fará o
interpretador LISP ser um plugue, e não uma reescrita.

**3. Undo sobre o ezdxf sem clonar documento.**
`unlink_entity` tira a entidade do layout mas a mantém viva; refazer é só
religá-la. `core/undo.py` guarda apenas isso.

```
engecad/
  core/        geometria, documento, CRS, undo, índice espacial, registro   (SEM Qt)
  render/      viewport, canvas, camada raster, tema
  io/          DXF, sidecar .emap.json, importação de raster (cadeia ECW)
  tools/       ferramentas interativas (máquinas de estado)
  snap/        motor de osnap
  ui/          janela, linha de comando, painel de camadas, diálogo de CRS
  scripting/   API pública e console
```

`core/` não importa Qt — toda a geometria, CRS e undo são testáveis sem abrir
janela.

---

## Testes

```bash
pytest -q
```

111 testes. Os que mais importam:

- ida e volta `mundo → tela → mundo` com coordenada UTM real (erro exigido < 1 mm; medido: 0);
- zoom ancorado 60× sem deriva do ponto sob o cursor;
- round-trip DXF: criar → salvar → reabrir → geometria idêntica;
- snap escolhendo o candidato certo por prioridade e por raio em pixels;
- script no console desfazendo num passo, e script que falha não deixando resíduo;
- **desenhar sobre a ortofoto e conferir que o DXF salvo tem a coordenada do mundo real.**

Os testes de GUI sobem a aplicação inteira em modo offscreen e chegam a
verificar os pixels pintados.

---

## Roteiro

| Versão | Entrega |
|---|---|
| **0.1** | *(atual)* ver, desenhar, salvar, console Python |
| 0.2 | seleção, grips, `MOVE COPY ROTATE OFFSET TRIM EXTEND ERASE`, `ARC CIRCLE TEXT` |
| 0.3 | plantas cadastrais: hachura, cotas, blocos, quadro de áreas, memorial descritivo |
| 0.4 | **AutoLISP**: interpretador próprio + `command`, `entmake`, `ssget`, carga de `.lsp` |
| 0.5 | espaço papel, escala de plotagem, carimbo, PDF georreferenciado |
| 0.6 | SHP / GeoPackage / KML, tiles XYZ e WMS |
| 0.7 | instalador Windows, sistema de plugins |

---

## Construído sobre

[PySide6](https://doc.qt.io/qtforpython-6/) (LGPL) ·
[ezdxf](https://ezdxf.mozman.at/) (MIT) ·
[pyproj](https://pyproj4.github.io/pyproj/) (MIT) ·
[rasterio](https://rasterio.readthedocs.io/) (BSD) ·
[NumPy](https://numpy.org/) · [Shapely](https://shapely.readthedocs.io/)
