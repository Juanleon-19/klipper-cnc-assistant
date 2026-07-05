import requests

MOONRAKER_URL = "http://192.168.0.21:7126"

print("=" * 45)
print("KLIPPER CNC ASSISTANT")
print("=" * 45)

try:
    response = requests.get(
        f"{MOONRAKER_URL}/server/info",
        timeout=3
    )

    response.raise_for_status()
    server_info = response.json()["result"]

except requests.RequestException as error:
    print("\n[ERROR] No fue posible conectar con Moonraker")
    print(error)
    raise SystemExit(1)


print("\n[MOONRAKER]")

print(
    "Klipper conectado:",
    server_info.get("klippy_connected")
)

print(
    "Estado Klipper:",
    server_info.get("klippy_state")
)


if server_info.get("klippy_state") != "ready":
    print("\n[ERROR] Klipper no esta listo")
    raise SystemExit(1)


try:
    response = requests.get(
        f"{MOONRAKER_URL}/printer/objects/query",
        params={
            "gcode_move": "position",
            "toolhead": "position"
        },
        timeout=3
    )

    response.raise_for_status()

    status = response.json()["result"]["status"]

except requests.RequestException as error:
    print("\n[ERROR] No fue posible leer la posicion")
    print(error)
    raise SystemExit(1)


gcode_position = status["gcode_move"]["position"]
toolhead_position = status["toolhead"]["position"]


print("\n[POSICION G-CODE]")

print(f"X = {gcode_position[0]:.3f} mm")
print(f"Y = {gcode_position[1]:.3f} mm")
print(f"Z = {gcode_position[2]:.3f} mm")


print("\n[POSICION TOOLHEAD]")

print(f"X = {toolhead_position[0]:.3f} mm")
print(f"Y = {toolhead_position[1]:.3f} mm")
print(f"Z = {toolhead_position[2]:.3f} mm")


print("\n[OK] Comunicacion con CNC establecida")
