"""Exemplo v0.2: gera uma quadra e depois EDITA por script.

Mostra a API de edicao (move, copy, rotate, offset) trabalhando junto com a
selecao. Como sempre, tudo isso desfaz com um unico Ctrl+Z.

Rode com  Ferramentas > Executar script .py,  ou cole no console (F9).
"""

E, N = 500000.0, 7400000.0

LARGURA = 12.0
PROFUNDIDADE = 30.0
N_LOTES = 6
RECUO = 4.0

new_layer("QUADRA", 1)
new_layer("LOTE", 2)
new_layer("RECUO", 4)
new_layer("CALCADA", 3)

# --- um lote modelo, criado uma vez ---
set_layer("LOTE")
modelo = add_polyline(
    [(E, N), (E + LARGURA, N), (E + LARGURA, N + PROFUNDIDADE), (E, N + PROFUNDIDADE)],
    closed=True,
)

# --- repetido lateralmente com copy() ---
lotes = [modelo]
for i in range(1, N_LOTES):
    lotes.extend(copy(modelo, LARGURA * i, 0))
print(f"{len(lotes)} lotes por copia lateral")

# --- linha de recuo por paralela da testada ---
set_layer("RECUO")
testada = add_line((E, N), (E + LARGURA * N_LOTES, N))
recuo = offset(testada, RECUO)  # positivo = para a esquerda do trajeto (para cima)
print(f"recuo criado a {RECUO} m da testada")

# --- calcada: outra paralela, do lado de fora ---
set_layer("CALCADA")
calcada = offset(testada, -2.5)

# --- perimetro da quadra ---
set_layer("QUADRA")
add_polyline(
    [
        (E, N),
        (E + LARGURA * N_LOTES, N),
        (E + LARGURA * N_LOTES, N + PROFUNDIDADE),
        (E, N + PROFUNDIDADE),
    ],
    closed=True,
)

# --- uma copia da quadra inteira, girada 90 graus, formando a esquina ---
select(lotes)
bloco = copy(selected(), 0, PROFUNDIDADE + 14)
rotate(bloco, (E, N + PROFUNDIDADE + 14), 0)  # sem giro; troque para 90 para ver
print(f"{len(bloco)} lotes copiados para a quadra de cima")

# --- rotulos ---
set_layer("0")
area_lote = LARGURA * PROFUNDIDADE
for i in range(N_LOTES):
    x = E + i * LARGURA + 1.0
    add_text(f"L{i + 1:02d}", (x, N + PROFUNDIDADE - 3.0), height=1.4)
    add_text(f"{area_lote:.0f} m2", (x, N + PROFUNDIDADE - 5.0), height=0.9)

deselect()
zoom_extents()

print(f"area por lote: {area_lote:.2f} m2")
print(f"total no desenho: {count()} entidades")
message(f"Quadra dupla: {N_LOTES * 2} lotes de {area_lote:.0f} m2")
