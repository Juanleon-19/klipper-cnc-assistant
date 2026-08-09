# Moonraker upload timeout hotfix

## Problema

Las consultas normales a Moonraker usan `MOONRAKER_REQUEST_TIMEOUT`, cuyo valor predeterminado es 2 s. El cliente de subida reutilizaba ese mismo timeout para `POST /server/files/upload` incluso cuando el archivo compensado podia ser grande y la peticion incluia `print=true`.

Un timeout del cliente no demuestra que Moonraker haya rechazado la subida. Reintentar automaticamente un POST de subida+inicio seria inseguro porque la primera peticion podria haber sido aceptada por el servidor.

## Contrato

- Las consultas HTTP normales conservan su timeout configurado.
- Las subidas disponen de un piso de timeout mayor (`30 s`) sin modificar los timeouts de movimiento o telemetria.
- No hay reintento automatico de subida.
- El cambio no ejecuta hardware ni G-code durante las pruebas.
