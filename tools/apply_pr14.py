from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(relative: str, old: str, new: str) -> None:
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"ABORT: {relative}: expected exactly one match, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"patched {relative}")


def insert_before_final_describe_close(relative: str, addition: str) -> None:
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    marker = "\n});"
    index = text.rfind(marker)
    if index < 0 or text[index:].strip() != "});":
        raise SystemExit(f"ABORT: {relative}: final describe close not found")
    path.write_text(text[:index] + addition + text[index:], encoding="utf-8")
    print(f"patched {relative}")


workspace = "frontend/src/features/projects/ProjectWorkspace.tsx"

replace_once(
    workspace,
    '''  const [heightMapBusy, setHeightMapBusy] = useState(false);\n  const [previewBusy, setPreviewBusy] = useState(false);\n  const [mapActionBusy, setMapActionBusy] = useState(false);\n  const [suggestionBusy, setSuggestionBusy] = useState(false);\n  const [referenceBusy, setReferenceBusy] = useState(false);''',
    '''  const [heightMapBusy, setHeightMapBusy] = useState(false);\n  const [previewBusy, setPreviewBusy] = useState(false);\n  const [armMeshBusy, setArmMeshBusy] = useState(false);\n  const [mapActionBusy, setMapActionBusy] = useState(false);\n  const [suggestionBusy, setSuggestionBusy] = useState(false);\n  const [referenceBusy, setReferenceBusy] = useState(false);\n  const [compensationBusy, setCompensationBusy] = useState(false);''',
)

replace_once(
    workspace,
    '''  const referenceMoveInFlight = useRef(false);\n  const startJobInFlight = useRef(false);''',
    '''  const referenceMoveInFlight = useRef(false);\n  const armMeshInFlight = useRef(false);\n  const compensationInFlight = useRef(false);\n  const startJobInFlight = useRef(false);''',
)

replace_once(
    workspace,
    '''    const previewRequestDurationMs = typeof meshPreview?.preview_request_duration_ms === "number" ? meshPreview.preview_request_duration_ms : null;\n    const previewBackendDurationMs = typeof meshPreview?.preview_backend_duration_ms === "number" ? meshPreview.preview_backend_duration_ms : null;\n    const physicalMapCancelled = physicalMap?.status === "CANCELLED";\n    const startMapDisabled = mapActionBusy || !physicalMapId || !physicalMap || Boolean(meshPreview) || isPhysicalMapReady(physicalMap) || physicalMapCancelled;''',
    '''    const previewRequestDurationMs = typeof meshPreview?.preview_request_duration_ms === "number" ? meshPreview.preview_request_duration_ms : null;\n    const previewBackendDurationMs = typeof meshPreview?.preview_backend_duration_ms === "number" ? meshPreview.preview_backend_duration_ms : null;\n    const armRequestDurationMs = typeof physicalMap?.arm_request_duration_ms === "number" ? physicalMap.arm_request_duration_ms : null;\n    const armBackendDurationMs = typeof physicalMap?.arm_backend_duration_ms === "number" ? physicalMap.arm_backend_duration_ms : null;\n    const physicalMapCancelled = physicalMap?.status === "CANCELLED";\n    const startMapDisabled = armMeshBusy || mapActionBusy || !physicalMapId || !physicalMap || Boolean(meshPreview) || isPhysicalMapReady(physicalMap) || physicalMapCancelled;''',
)

replace_once(
    workspace,
    '''    const armMeshPreview = async () => {\n      if (!selectedOperation || !meshPreview || !currentMeshFingerprint || !physicalReady) {\n        return;\n      }\n      if (meshPreviewFingerprint !== currentMeshFingerprint) {\n        setMeshValidationMessage("La configuración cambió después de la preview. Regénere la vista previa antes de armar.");\n        return;\n      }\n      await withPhysicalMapAction(async () => {\n        const persisted = (await api.planPhysicalMapFromReference(project.id, selectedOperation.id, physicalPlanPayload)).payload;\n        const comparison = previewMatchesPersisted(meshPreview, persisted);\n        if (!comparison.matches) {\n          throw new Error(buildPreviewMismatchMessage(comparison.differingFields));\n        }\n        setActiveMapTab("mapa2d");\n        setMeshValidationMessage("Sondeo armado. El mapa persistido coincide con la vista previa actual y queda listo para iniciar.");\n        return persisted;\n      }, { clearPreview: true });\n    };''',
    '''    const armMeshPreview = async () => {\n      if (armMeshInFlight.current || !selectedOperation || !meshPreview || !currentMeshFingerprint || !physicalReady) {\n        return;\n      }\n      if (meshPreviewFingerprint !== currentMeshFingerprint) {\n        setMeshValidationMessage("La configuración cambió después de la preview. Regénere la vista previa antes de armar.");\n        return;\n      }\n      armMeshInFlight.current = true;\n      setArmMeshBusy(true);\n      const startedAt = performance.now();\n      try {\n        await withPhysicalMapAction(async () => {\n          const response = await api.planPhysicalMapFromReference(project.id, selectedOperation.id, physicalPlanPayload);\n          const persisted: PhysicalMapPayload = {\n            ...response.payload,\n            arm_request_duration_ms: Number((performance.now() - startedAt).toFixed(3)),\n          };\n          const comparison = previewMatchesPersisted(meshPreview, persisted);\n          if (!comparison.matches) {\n            throw new Error(buildPreviewMismatchMessage(comparison.differingFields));\n          }\n          setActiveMapTab("mapa2d");\n          setMeshValidationMessage("Sondeo armado. El mapa persistido coincide con la vista previa actual y queda listo para iniciar.");\n          return persisted;\n        }, { clearPreview: true });\n      } finally {\n        armMeshInFlight.current = false;\n        setArmMeshBusy(false);\n      }\n    };''',
)

replace_once(
    workspace,
    '''              <div className="metric-box"><span>Preview request</span><strong>{typeof previewRequestDurationMs === "number" ? `${previewRequestDurationMs.toFixed(1)} ms` : "-"}</strong></div>\n              <div className="metric-box"><span>Preview backend</span><strong>{typeof previewBackendDurationMs === "number" ? `${previewBackendDurationMs.toFixed(1)} ms` : "-"}</strong></div>''',
    '''              <div className="metric-box"><span>Preview request</span><strong>{typeof previewRequestDurationMs === "number" ? `${previewRequestDurationMs.toFixed(1)} ms` : "-"}</strong></div>\n              <div className="metric-box"><span>Preview backend</span><strong>{typeof previewBackendDurationMs === "number" ? `${previewBackendDurationMs.toFixed(1)} ms` : "-"}</strong></div>\n              {typeof armRequestDurationMs === "number" ? <div className="metric-box"><span>Armado total</span><strong>{armRequestDurationMs.toFixed(1)} ms</strong></div> : null}\n              {typeof armBackendDurationMs === "number" ? <div className="metric-box"><span>Armado servidor</span><strong>{armBackendDurationMs.toFixed(1)} ms</strong></div> : null}''',
)

replace_once(
    workspace,
    '''              <button className="button" type="button" disabled={previewBusy || !selectedOperation || !meshConfigValid} onClick={() => void requestMeshPreview()}>{previewBusy ? "Generando vista previa…" : "1. Generar vista previa de malla"}</button>\n              {previewBusy ? <button className="button button--ghost" type="button" onClick={() => clearMeshPreview("Generación de vista previa cancelada.")}>Cancelar generación</button> : null}\n              <button className="button" type="button" disabled={!meshPreview} onClick={() => setMeshValidationMessage(physicalFailedPoints > 0 ? `La malla tiene ${physicalFailedPoints} punto(s) fallidos o pendientes de reintento.` : "Cobertura geométrica revisada. No se extrapola fuera de la región interior ni sobre exclusiones.")}>2. Validar límites</button>\n              <button className="button" type="button" disabled={!meshPreview || !physicalReady || meshPreviewFingerprint !== currentMeshFingerprint || physicalMap?.status === "MESH_COMPLETE"} onClick={() => void armMeshPreview()}>3. Armar sondeo</button>\n              <button className="button" type="button" disabled={startMapDisabled} onClick={() => void withPhysicalMapAction(async () => (await api.executeAllPhysicalMapPoints(project.id, physicalMapId)).payload)}>{mapActionBusy ? "Iniciando sondeo…" : "4. Iniciar sondeo automático"}</button>''',
    '''              <button className="button" type="button" disabled={previewBusy || armMeshBusy || !selectedOperation || !meshConfigValid} onClick={() => void requestMeshPreview()}>{previewBusy ? "Generando vista previa…" : "1. Generar vista previa de malla"}</button>\n              {previewBusy ? <button className="button button--ghost" type="button" onClick={() => clearMeshPreview("Generación de vista previa cancelada.")}>Cancelar generación</button> : null}\n              <button className="button" type="button" disabled={armMeshBusy || !meshPreview} onClick={() => setMeshValidationMessage(physicalFailedPoints > 0 ? `La malla tiene ${physicalFailedPoints} punto(s) fallidos o pendientes de reintento.` : "Cobertura geométrica revisada. No se extrapola fuera de la región interior ni sobre exclusiones.")}>2. Validar límites</button>\n              <button className="button" type="button" disabled={armMeshBusy || mapActionBusy || !meshPreview || !physicalReady || meshPreviewFingerprint !== currentMeshFingerprint || physicalMap?.status === "MESH_COMPLETE"} onClick={() => void armMeshPreview()}>{armMeshBusy ? "Armando sondeo…" : "3. Armar sondeo"}</button>\n              <button className="button" type="button" disabled={startMapDisabled} onClick={() => void withPhysicalMapAction(async () => (await api.executeAllPhysicalMapPoints(project.id, physicalMapId)).payload)}>{mapActionBusy && !armMeshBusy ? "Iniciando sondeo…" : "4. Iniciar sondeo automático"}</button>''',
)

replace_once(
    workspace,
    '''              <button className="button button--ghost" type="button" disabled={!meshPreview && !previewBusy} onClick={() => clearMeshPreview("Vista previa limpia. La configuración, la referencia y los mapas persistidos se conservaron.")}>Limpiar vista previa</button>''',
    '''              <button className="button button--ghost" type="button" disabled={armMeshBusy || (!meshPreview && !previewBusy)} onClick={() => clearMeshPreview("Vista previa limpia. La configuración, la referencia y los mapas persistidos se conservaron.")}>Limpiar vista previa</button>''',
)

replace_once(
    workspace,
    '''  const generateProjectCompensation = async () => {\n    if (!project || !selectedSetup || !activeJobFace) {\n      return;\n    }\n    setReferenceBusy(true);\n    setWorkspaceError("");\n    try {\n      const plan = await api.generateProjectCompensation(project.id, selectedSetup.id, activeJobFace);\n      setJobPlan(plan);\n      setLiveExecution(await api.getLiveExecution(project.id, selectedSetup.id, activeJobFace));\n    } catch (error) {\n      setWorkspaceError(error instanceof Error ? error.message : "No fue posible generar la compensación del proyecto.");\n    } finally {\n      setReferenceBusy(false);\n    }\n  };''',
    '''  const generateProjectCompensation = async () => {\n    if (compensationInFlight.current || !project || !selectedSetup || !activeJobFace) {\n      return;\n    }\n    compensationInFlight.current = true;\n    setCompensationBusy(true);\n    setWorkspaceError("");\n    try {\n      const plan = await api.generateProjectCompensation(project.id, selectedSetup.id, activeJobFace);\n      setJobPlan(plan);\n      setLiveExecution(await api.getLiveExecution(project.id, selectedSetup.id, activeJobFace));\n    } catch (error) {\n      setWorkspaceError(error instanceof Error ? error.message : "No fue posible generar la compensación del proyecto.");\n    } finally {\n      compensationInFlight.current = false;\n      setCompensationBusy(false);\n    }\n  };''',
)

replace_once(
    workspace,
    '''    const adaptiveExecutable = compensationAudit?.adaptive_fast?.executable !== false;\n    const adaptiveDownloadLabel = adaptiveExecutable ? "Descargar adaptive_fast" : "Descargar adaptive experimental";''',
    '''    const adaptiveExecutable = compensationAudit?.adaptive_fast?.executable !== false;\n    const adaptiveDownloadLabel = adaptiveExecutable ? "Descargar adaptive_fast" : "Descargar adaptive experimental";\n    const compensationControlsBusy = referenceBusy || compensationBusy;''',
)

replace_once(
    workspace,
    '''            <button className="button button--ghost" type="button" disabled={referenceBusy} onClick={() => void prepareJobRun()}>Revalidar plan</button>\n            <button className="button" type="button" disabled={referenceBusy} onClick={() => void generateProjectCompensation()}>Generar compensación del proyecto</button>''',
    '''            <button className="button button--ghost" type="button" disabled={compensationControlsBusy} onClick={() => void prepareJobRun()}>Revalidar plan</button>\n            <button className="button" type="button" disabled={compensationControlsBusy} onClick={() => void generateProjectCompensation()}>{compensationBusy ? "Generando compensación…" : "Generar compensación del proyecto"}</button>''',
)

replace_once(
    workspace,
    '''              <button className={`button${currentCompensationMode === "legacy" ? "" : " button--ghost"}`} type="button" disabled={referenceBusy} onClick={() => void updateCompensationSettings({ compensation_mode: "legacy" })}>Legacy</button>\n              <button className={`button${currentCompensationMode === "adaptive_fast" ? "" : " button--ghost"}`} type="button" disabled={referenceBusy || !adaptiveExecutable} onClick={() => void updateCompensationSettings({ compensation_mode: "adaptive_fast" })}>Adaptativa rápida</button>''',
    '''              <button className={`button${currentCompensationMode === "legacy" ? "" : " button--ghost"}`} type="button" disabled={compensationControlsBusy} onClick={() => void updateCompensationSettings({ compensation_mode: "legacy" })}>Legacy</button>\n              <button className={`button${currentCompensationMode === "adaptive_fast" ? "" : " button--ghost"}`} type="button" disabled={compensationControlsBusy || !adaptiveExecutable} onClick={() => void updateCompensationSettings({ compensation_mode: "adaptive_fast" })}>Adaptativa rápida</button>''',
)

replace_once(
    workspace,
    '''              <button className="button button--ghost" type="button" disabled={compensationAuditBusy || referenceBusy} onClick={() => void refreshCompensationAudit()}>Recalcular auditoría</button>\n              <button className="button button--ghost" type="button" disabled={referenceBusy} onClick={() => void downloadCompensatedArtifact("legacy")}>Descargar legacy</button>\n              <button className="button button--ghost" type="button" disabled={referenceBusy} onClick={() => void downloadCompensatedArtifact("adaptive_fast")}>{adaptiveDownloadLabel}</button>''',
    '''              <button className="button button--ghost" type="button" disabled={compensationAuditBusy || compensationControlsBusy} onClick={() => void refreshCompensationAudit()}>Recalcular auditoría</button>\n              <button className="button button--ghost" type="button" disabled={compensationControlsBusy} onClick={() => void downloadCompensatedArtifact("legacy")}>Descargar legacy</button>\n              <button className="button button--ghost" type="button" disabled={compensationControlsBusy} onClick={() => void downloadCompensatedArtifact("adaptive_fast")}>{adaptiveDownloadLabel}</button>''',
)

replace_once(
    "frontend/src/types.ts",
    '''  point_count?: number;\n  excluded_count?: number;''',
    '''  point_count?: number;\n  preview_request_duration_ms?: number;\n  preview_backend_duration_ms?: number;\n  arm_request_duration_ms?: number;\n  arm_backend_duration_ms?: number;\n  arm_point_count?: number;\n  excluded_count?: number;''',
)

replace_once(
    "src/klipper_cnc_assistant/api/routes.py",
    '''        plan = physical_map_service.plan_from_saved_reference(\n            project_id=project_id,\n            operation_id=operation_id,\n            config=config,\n        )\n        return _physical_map_response(request, plan)''',
    '''        started_at = perf_counter()\n        plan = physical_map_service.plan_from_saved_reference(\n            project_id=project_id,\n            operation_id=operation_id,\n            config=config,\n        )\n        response_plan = dict(plan)\n        response_plan["arm_backend_duration_ms"] = round((perf_counter() - started_at) * 1000.0, 3)\n        response_plan["arm_point_count"] = int(response_plan.get("point_count") or len(response_plan.get("points") or []))\n        return _physical_map_response(request, response_plan)''',
)

replace_once(
    "tests/test_api.py",
    '''        self.assertEqual(measured["point_count"], 12)\n        self.assertEqual(measured["mesh_config"]["edge_margin_left_mm"], 1.5)''',
    '''        self.assertEqual(measured["point_count"], 12)\n        self.assertGreaterEqual(measured["arm_backend_duration_ms"], 0)\n        self.assertEqual(measured["arm_point_count"], 12)\n        self.assertEqual(measured["mesh_config"]["edge_margin_left_mm"], 1.5)''',
)

frontend_tests = r'''

  it("muestra Armando sondeo, bloquea doble POST y conserva la ejecución física separada", async () => {
    const pendingArm = deferred<{ payload: Record<string, unknown> }>();
    apiMock.planPhysicalMapFromReference.mockReturnValueOnce(pendingArm.promise);
    renderWorkspace(physicalMachine);

    fireEvent.click(screen.getByRole("button", { name: /Mapa de alturas/i }));
    await screen.findByText(/Mapa medido físicamente/i);
    fireEvent.change(screen.getByLabelText(/^Filas$/i), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText(/^Columnas$/i), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: /Generar vista previa de malla/i }));
    await waitFor(() => expect(apiMock.previewPhysicalMap).toHaveBeenCalledTimes(1));

    const armButton = await screen.findByRole("button", { name: /^3\. Armar sondeo$/i });
    await waitFor(() => expect(armButton).toBeEnabled());
    fireEvent.click(armButton);
    fireEvent.click(armButton);

    expect(await screen.findByRole("button", { name: /Armando sondeo…/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /^4\. Iniciar sondeo automático$/i })).toBeDisabled();
    expect(apiMock.planPhysicalMapFromReference).toHaveBeenCalledTimes(1);
    expect(apiMock.executeAllPhysicalMapPoints).not.toHaveBeenCalled();

    await act(async () => {
      pendingArm.resolve({
        payload: {
          map_id: "measured/manual-2x2",
          status: "MESH_PLANNED",
          source: "MEASURED",
          point_count: 4,
          mesh_configuration_fingerprint: "fingerprint/manual-2x2/config",
          mesh_geometry_fingerprint: "fingerprint/manual-2x2/geometry",
          arm_backend_duration_ms: 12.5,
          arm_point_count: 4,
        },
      });
      await pendingArm.promise;
    });

    await waitFor(() => expect(screen.queryByRole("button", { name: /Armando sondeo…/i })).toBeNull());
    expect(apiMock.planPhysicalMapFromReference).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Armado servidor")).toBeInTheDocument();
    expect(screen.getByText("12.5 ms")).toBeInTheDocument();
    expect(screen.getByText("Armado total")).toBeInTheDocument();
  });

  it("libera Armar sondeo tras error y permite reintento manual", async () => {
    const pendingArm = deferred<{ payload: Record<string, unknown> }>();
    apiMock.planPhysicalMapFromReference.mockReturnValueOnce(pendingArm.promise);
    renderWorkspace(physicalMachine);

    fireEvent.click(screen.getByRole("button", { name: /Mapa de alturas/i }));
    await screen.findByText(/Mapa medido físicamente/i);
    fireEvent.change(screen.getByLabelText(/^Filas$/i), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText(/^Columnas$/i), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: /Generar vista previa de malla/i }));
    await waitFor(() => expect(apiMock.previewPhysicalMap).toHaveBeenCalledTimes(1));
    fireEvent.click(await screen.findByRole("button", { name: /^3\. Armar sondeo$/i }));

    await act(async () => {
      pendingArm.reject(new Error("Fallo controlado al armar sondeo."));
      try {
        await pendingArm.promise;
      } catch {
        // expected rejection
      }
    });

    expect(await screen.findByText(/Fallo controlado al armar sondeo/i)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: /^3\. Armar sondeo$/i })).toBeEnabled());
    expect(apiMock.executeAllPhysicalMapPoints).not.toHaveBeenCalled();
  });

  it("muestra Generando compensación y evita doble generación", async () => {
    const pendingCompensation = deferred<JobPlan>();
    apiMock.generateProjectCompensation.mockReturnValueOnce(pendingCompensation.promise);
    renderWorkspace(physicalMachine);

    fireEvent.click(screen.getByRole("button", { name: /^Ejecución$/i }));
    const generateButton = await screen.findByRole("button", { name: /Generar compensación del proyecto/i });
    fireEvent.click(generateButton);
    fireEvent.click(generateButton);

    expect(await screen.findByRole("button", { name: /Generando compensación…/i })).toBeDisabled();
    expect(apiMock.generateProjectCompensation).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: /Revalidar plan/i })).toBeDisabled();

    await act(async () => {
      pendingCompensation.resolve(jobPlan);
      await pendingCompensation.promise;
    });

    await waitFor(() => expect(screen.getByRole("button", { name: /Generar compensación del proyecto/i })).toBeEnabled());
    expect(apiMock.generateProjectCompensation).toHaveBeenCalledTimes(1);
  });

  it("libera Generar compensación tras error sin retry automático", async () => {
    const pendingCompensation = deferred<JobPlan>();
    apiMock.generateProjectCompensation.mockReturnValueOnce(pendingCompensation.promise);
    renderWorkspace(physicalMachine);

    fireEvent.click(screen.getByRole("button", { name: /^Ejecución$/i }));
    fireEvent.click(await screen.findByRole("button", { name: /Generar compensación del proyecto/i }));

    await act(async () => {
      pendingCompensation.reject(new Error("Fallo controlado de compensación."));
      try {
        await pendingCompensation.promise;
      } catch {
        // expected rejection
      }
    });

    expect(await screen.findByText(/Fallo controlado de compensación/i)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: /Generar compensación del proyecto/i })).toBeEnabled());
    expect(apiMock.generateProjectCompensation).toHaveBeenCalledTimes(1);
  });
'''

insert_before_final_describe_close(
    "frontend/src/features/projects/ProjectWorkspace.test.tsx",
    frontend_tests,
)

print("PR14 patch applied successfully. No hardware commands were executed.")
