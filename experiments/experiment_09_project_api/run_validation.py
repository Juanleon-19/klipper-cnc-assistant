from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from klipper_cnc_assistant.api import create_app


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    runtime_dir = base_dir / "runtime_data"
    runtime_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    client = TestClient(
        create_app(data_dir=runtime_dir)
    )

    create_response = client.post(
        "/api/projects",
        json={
            "nombre": "Validacion API PCB",
            "material": {
                "ancho_mm": 40.0,
                "alto_mm": 30.0,
                "espesor_mm": 1.6,
            },
            "doble_cara": True,
            "eje_volteo": "y",
            "agujeros_alineacion": [
                {
                    "x_mm": 3.0,
                    "y_mm": 3.0,
                    "diametro_mm": 3.0,
                },
                {
                    "x_mm": 33.0,
                    "y_mm": 3.0,
                    "diametro_mm": 3.0,
                },
            ],
        },
    )
    create_response.raise_for_status()
    project = create_response.json()
    project_id = project["id"]

    top_operation = client.post(
        f"/api/projects/{project_id}/operations",
        json={
            "nombre": "Cara superior",
            "tipo": "aislamiento",
            "cara": "superior",
            "orden": 0,
            "herramienta": "V-bit 30",
        },
    )
    top_operation.raise_for_status()
    top_operation_id = top_operation.json()["id"]

    bottom_operation = client.post(
        f"/api/projects/{project_id}/operations",
        json={
            "nombre": "Cara inferior",
            "tipo": "taladrado",
            "cara": "inferior",
            "orden": 1,
            "herramienta": "Broca 0.8",
        },
    )
    bottom_operation.raise_for_status()
    bottom_operation_id = bottom_operation.json()["id"]

    top_gcode = (
        base_dir / "sample_top.nc"
    ).read_text(encoding="utf-8")
    bottom_gcode = (
        base_dir / "sample_bottom.nc"
    ).read_text(encoding="utf-8")

    client.post(
        f"/api/projects/{project_id}/operations/{top_operation_id}/gcode",
        json={
            "nombre_archivo": "sample_top.nc",
            "contenido": top_gcode,
        },
    ).raise_for_status()
    client.post(
        f"/api/projects/{project_id}/operations/{bottom_operation_id}/gcode",
        json={
            "nombre_archivo": "sample_bottom.nc",
            "contenido": bottom_gcode,
        },
    ).raise_for_status()

    top_analysis = client.post(
        f"/api/projects/{project_id}/operations/{top_operation_id}/analyze"
    )
    top_analysis.raise_for_status()
    bottom_analysis = client.post(
        f"/api/projects/{project_id}/operations/{bottom_operation_id}/analyze"
    )
    bottom_analysis.raise_for_status()

    session = client.get("/api/machine/session")
    session.raise_for_status()

    result = {
        "project_id": project_id,
        "top_analysis": top_analysis.json(),
        "bottom_analysis": bottom_analysis.json(),
        "machine_session": session.json(),
    }
    output_path = base_dir / "results.json"
    output_path.write_text(
        json.dumps(
            result,
            ensure_ascii=True,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(output_path)


if __name__ == "__main__":
    main()
