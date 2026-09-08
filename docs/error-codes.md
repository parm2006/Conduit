# Conduit Error Codes

Client-specific codes end with the Client name's first initial. For example, `14.X` means Client X caused error 14. If the Client name is unknown, Conduit uses `.X`.

| Error code | Specific cause |
| --- | --- |
| `1` | The Server port is invalid. |
| `2` | The Client port is invalid. |
| `3` | The Server password is missing. |
| `4` | The Client password is missing. |
| `5` | The user cancelled firewall configuration from the Configure action. |
| `6` | Firewall configuration from the Configure action failed. |
| `7` | The user cancelled required firewall configuration while starting the Server. |
| `8` | Required firewall configuration failed while starting the Server. |
| `9` | The Server could not start or listen on its configured ports. |
| `10` | The remote viewer could not start, so the Server stopped. |
| `11.C` | Client C could not connect or authenticate with the Server. |
| `12.C` | Client C reported that its display arrangement changed. |
| `13` | The Server could not rescan its local displays. |
| `14.C` | Conduit could not rescan Client C's displays. |
| `15.C` | The display rescan request for Client C timed out. |
| `16.C` | Client C connected, but no free topology position was available. |
| `17` | The topology is disconnected because one or more Clients do not share a full grid edge. |
| `18` | The Server could not apply the topology locally. |
| `19.C` | Client C rejected or could not apply the topology. |
| `20.C` | Client C disconnected and invalidated the active mouse-routing topology. |
| `21` | Conduit could not clear the saved pairing identity. |
| `22.C` | Remote video from Client C became unavailable. |
| `XX` | A problem reached the console without a valid assigned error code. |
