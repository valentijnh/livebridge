# LiveBridge — Plan

**Doel:** één plugin waarmee Claude alles in Ableton Live 12.4.5 kan doen: tracks, clips, noten,
devices, VST/AU‑plugin‑parameters, browser (instrumenten, effecten, plugins, samples, packs, Splice),
automation, arrangement, scenes, mixer, routing, opnemen en navigatie. Werkt op **Windows en Mac**,
in elke Live‑editie, met Live op dezelfde machine of op een andere machine in je netwerk.

## Onderzoek (10 sept 2026) — conclusies

| Optie | Verdict |
|---|---|
| "GPT‑6 Astra maakt muziek in Ableton" | Combinatie van Computer Use + de open‑source Max for Live OSC‑brug `codex-live-bridge` + Ableton Extensions SDK. Geen eigen magie. |
| Ableton Extensions SDK (officieel, juni 2026) | JS/Node, alleen Suite, beta, extensies starten niet automatisch. Mooi als laag 2 (audio renderen), niet als basis. |
| Remote Script + MCP‑server (ahujasid, jpoindexter, bschoepke, Ziforge) | Bewezen, Win+Mac, elke editie, geen Max for Live nodig. **Dit is onze basis.** |
| Producer Pal | Max for Live‑device (GPL). Suite‑only. Niet nodig. |
| Splice | Officiële remote MCP `https://mcp.splice.com/mcp`: zoeken gratis, downloaden met jouw Creator‑plan (100/24u). Niet nabouwen, alleen koppelen + "sample in track"‑tool. |

## Architectuur (kort)

1. **Remote Script `LiveBridge`** (Python, draait ín Live): TCP JSON‑server op een achtergrondthread,
   alle commando's op Live's main thread. Generieke toegang tot het hele Live Object Model + Python‑eval.
2. **MCP‑server `livebridge-mcp`** (Python/FastMCP): ~100 tools voor Claude Code en Claude Desktop,
   automatische reconnect, LAN‑discovery via UDP‑beacon + token (voor Mac ↔ Windows wisselen).
3. **Installer** voor Mac en Windows: kopieert script, schrijft config, registreert MCP + Splice in
   Claude Desktop/Claude Code.
4. **Tests** met een nep‑`Live`‑module zodat alles buiten Live getest wordt, plus een
   integratiecheck voor op de echte machine.

Details: `docs/ARCHITECTURE.md` en `docs/PROTOCOL.md`.

## Bouwfasen (Workflow, Opus 5‑agents)

| Fase | Agents | Wat |
|---|---|---|
| 1. Fundament | 2 parallel | Remote Script‑kern (server, dispatcher, LOM‑resolver, serializer, eval) + Live‑stub; MCP‑kern (client, discovery, generieke LOM‑tools). |
| 2. Modules | 7 parallel | A transport/scenes/view · B tracks/mixer/routing/record · C clips/notes/arrangement · D devices/plugins/racks · E browser/samples/Splice · F automation/cues · G installers/docs/skill. |
| 3. Integratie | 1 | Alles samenvoegen, volledige testsuite groen, `docs/TOOLS.md` genereren. |
| 4. Review | 3 parallel | Lenzen: Live‑API/threading‑correctheid · MCP‑toolkwaliteit/tokenzuinigheid · installer/netwerk/security. |
| 5. Fix + verificatie | 2 | Bevindingen doorvoeren, testsuite en eindrapport. |

## Wat je daarna doet (op je Live‑machine)

```bash
python installers/install.py            # Mac of Windows; voegt --network toe voor LAN-modus
```
Dan in Live: Preferences → Link, Tempo & MIDI → Control Surface → **LiveBridge**. Klaar.

## Bewust buiten scope (fase 2)

- Audio exporteren/renderen, freeze/flatten, menu‑acties: niet in het Live Object Model. Kandidaat
  voor een Ableton Extensions SDK‑laag (Suite) of Computer Use.
- Plugin‑knoppen die niet in Live's "Configure"‑paneel staan zijn niet via de API bereikbaar.
- Max for Live audio‑tap (Claude "luistert" mee) — optioneel later.
