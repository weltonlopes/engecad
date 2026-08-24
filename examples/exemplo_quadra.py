"""Exemplo de script do EngeCAD: gera uma quadra com lotes e rotulos.

Rode com  Ferramentas > Executar script .py,  ou cole no console (F9).
Tudo isso desfaz com um unico Ctrl+Z.
"""

# Origem da quadra, em SIRGAS 2000 / UTM 22S.
E, N = 500000.0, 7400000.0

LARGURA_LOTE = 12.0
PROFUNDIDADE = 30.0
N_LOTES = 8
RECUO_FRONTAL = 4.0

new_layer("QUADRA", 1)
new_layer("LOTE", 2)
new_layer("RECUO", 4)
new_layer("ROTULO", 7)

# perimetro da quadra
largura_total = LARGURA_LOTE * N_LOTES
set_layer("QUADRA")
add_polyline(
    [
        (E, N),
        (E + largura_total, N),
        (E + largura_total, N + PROFUNDIDADE),
        (E, N + PROFUNDIDADE),
    ],
    closed=True,
)

area_total = 0.0
for i in range(N_LOTES):
    x0 = E + i * LARGURA_LOTE
    x1 = x0 + LARGURA_LOTE
    cantos = [(x0, N), (x1, N), (x1, N + PROFUNDIDADE), (x0, N + PROFUNDIDADE)]

    set_layer("LOTE")
    add_polyline(cantos, closed=True)

    # linha de recuo frontal
    set_layer("RECUO")
    add_polyline([(x0, N + RECUO_FRONTAL), (x1, N + RECUO_FRONTAL)])

    a = area(cantos)
    area_total += a
    set_layer("ROTULO")
    add_text(f"LOTE {i + 1:02d}", (x0 + 1.0, N + PROFUNDIDADE - 3.0), height=1.2)
    add_text(f"{a:.2f} m2", (x0 + 1.0, N + PROFUNDIDADE - 5.0), height=0.9)

zoom_extents()

print(f"{N_LOTES} lotes gerados")
print(f"Testada da quadra: {largura_total:.2f} m")
print(f"Area total: {area_total:.2f} m2  ({area_total / 10000:.4f} ha)")
lon, lat = to_wgs84(E, N)
print(f"Origem em WGS84: {lon:.6f}, {lat:.6f}")
message(f"Quadra com {N_LOTES} lotes, {area_total:.2f} m2")
