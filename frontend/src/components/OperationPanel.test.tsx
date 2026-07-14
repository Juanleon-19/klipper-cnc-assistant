        import { render, screen, waitFor } from "@testing-library/react";
        import userEvent from "@testing-library/user-event";
        import { describe, expect, it, vi } from "vitest";

        import { ProjectWorkspace } from "./ProjectWorkspace";

        vi.mock("../features/viewer/ToolpathViewer", () => ({
          ToolpathViewer: () => <div data-testid="toolpath-viewer">viewer</div>,
        }));

        const baseProject = {
          id: "proj_1",
          nombre: "PCB demo",
          material: { ancho_mm: 50, alto_mm: 40, espesor_mm: 1.6 },
          doble_cara: false,
          eje_volteo: null,
          agujeros_alineacion: [],
          operaciones: [
            {
              id: "op_1",
              nombre: "Fresado cara superior",
              tipo: "aislamiento",
              cara: "superior",
              orden: 0,
              archivo_gcode: null,
              nombre_archivo_original: null,
              tamano_archivo_bytes: null,
              sha256: null,
              herramienta: "V-bit 30",
              estado: "esperando archivo",
              analisis: null,
            },
          ],
          creado_en: new Date().toISOString(),
          actualizado_en: new Date().toISOString(),
          version_esquema: "1.2",
          estado_general: "esperando archivo",
        };

        describe("ProjectWorkspace", () => {
          it("permite cargar un archivo para la operación seleccionada", async () => {
            const user = userEvent.setup();
            const onUploadFile = vi.fn().mockResolvedValue(undefined);

            render(
              <ProjectWorkspace
                project={baseProject}
                busyKey={null}
                savingProject={false}
                onSaveProject={vi.fn().mockResolvedValue(undefined)}
                onAddOperation={vi.fn().mockResolvedValue(undefined)}
                onDeleteOperation={vi.fn().mockResolvedValue(undefined)}
                onRemoveFile={vi.fn().mockResolvedValue(undefined)}
                onAnalyze={vi.fn().mockResolvedValue(undefined)}
                onUploadFile={onUploadFile}
              />
            );

            const input = screen.getByLabelText(/Cargar archivo para Fresado cara superior/i);
            const file = new File(["G21\nG1 X1 Y1"], "sample_top.nc", { type: "text/plain" });
            await user.upload(input, file);

            await waitFor(() => {
              expect(onUploadFile).toHaveBeenCalled();
            });
            expect(onUploadFile.mock.calls[0][0].id).toBe("op_1");
            expect(onUploadFile.mock.calls[0][1].name).toBe("sample_top.nc");
          });

          it("indica que la cara inferior queda reservada en proyectos de una cara", () => {
            render(
              <ProjectWorkspace
                project={baseProject}
                busyKey={null}
                savingProject={false}
                onSaveProject={vi.fn().mockResolvedValue(undefined)}
                onAddOperation={vi.fn().mockResolvedValue(undefined)}
                onDeleteOperation={vi.fn().mockResolvedValue(undefined)}
                onRemoveFile={vi.fn().mockResolvedValue(undefined)}
                onAnalyze={vi.fn().mockResolvedValue(undefined)}
                onUploadFile={vi.fn().mockResolvedValue(undefined)}
              />
            );

            expect(screen.getByText(/Solo para PCB doble cara/i)).toBeInTheDocument();
          });
        });
