# Raven 1.0

Raven 1.0 je lokální desktopový AI pracovní prostor pro 64bitové Windows 10 a Windows 11. Má tmavé textové rozhraní, lokální historii chatů a projektů, přehled agentů, úkolů, telemetrie a procesů. Hlasové funkce byly z verze 1.0 zcela odstraněny.

## Nejjednodušší instalace

1. Na stránce [Releases](https://github.com/darver1970/raven/releases/tag/v1.0) stáhněte pouze `Raven-1.0-Setup.exe` z vydání 1.0.
2. Spusťte instalátor. Při prvním spuštění se Raven zeptá na jedinou pracovní složku; výchozí je `C:\Raven`. Do zvolené složky uloží zdroje, modely, runtime i data a připraví pouze bezplatné závislosti.
3. Dokončení první instalace může trvat déle kvůli stažení lokálního modelu. Potom spusťte zástupce **Raven 1.0** na ploše.

Instalace nikdy neaktivuje placené předplatné. Windows může při prvním spuštění zobrazit ochranu SmartScreen, protože komunitní sestavení není podepsané placeným certifikátem.

### Požadavky

- 64bitové Windows 10 nebo Windows 11;
- alespoň 24 GB volného místa;
- internet při první instalaci;
- Windows Package Manager (`winget`), který je běžnou součástí aktuálních Windows;
- pro lokální AI je doporučeno nejméně 16 GB RAM, základní cloudový režim může fungovat i na slabším PC.

### Ruční instalace ze zdrojů

Pokud nechcete použít EXE, stáhněte ZIP zdrojového kódu z GitHubu, rozbalte jej a v dané složce spusťte:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -InstallPath C:\Raven
```

Instalátor podle potřeby doplní Git, Node.js LTS, Ollamu, Python prostředí, agentní knihovny a desktopovou vrstvu. Hotovou aplikaci lze otevřít zástupcem `Raven 1.0` nebo souborem `C:\Raven\desktop\Raven-Desktop.exe`.

### Aktualizace

Před instalací nové verze zazálohujte vlastní důležitá data. Nové vydání stáhněte pouze z oficiálního GitHub repozitáře. API klíče a lokální historie jsou v `runtime/`, který se do GitHubu nikdy nenahrává.

## Modely a automatické přepínání

Automatický režim používá pevné pořadí:

1. Gemini Free
2. OpenRouter Free
3. další nakonfigurované free-only zálohy: Groq, Cerebras, Mistral, GitHub Models a volitelně Cloudflare Workers AI
4. lokální Ollama

Na další zdroj se přepne při vyčerpání bezplatné kvóty nebo při nedostupnosti služby. API klíče se ukládají šifrovaně přes Windows DPAPI a nejsou součástí repozitáře ani historie chatu. Ručně lze zvolit režim Automaticky, Lokálně, Rychlost, Kvalita, Výzkum nebo Kód.

## Hlavní části

- Chat: víceřádkový editor, Enter pro odeslání a Shift+Enter pro nový řádek, lokální historie, kopírování odpovědí a bloků kódu.
- Projekty: oddělené pracovní kontexty s cestou, Git repozitářem, technologiemi, poznámkami a testovacím příkazem.
- Agenti: větve Core, Planning, Research, Browser, Coding, Testing, Files, Memory, Security a System; výchozí Analytik s lokálním Qwen 3.5 9B, přidávání vlastních agentů, závislosti, průběh, živé stavy a samostatné okno.
- Systém: stabilní telemetrie bez problikávání, živé grafy CPU/RAM/GPU/disku/sítě, teploty, příkon, ventilátory, upozornění, nejzatíženější procesy a samostatný bar zaplnění každého disku. Procesy jsou seskupené jako ve Správci úloh a lze je filtrovat a řadit podle komponent.
- Úkoly: lokální historie požadavků, použitého poskytovatele a výsledku.
- Paměť: Memory Manager a lokální Project Indexer se SQLite FTS5, projektové poznatky a cílené mazání.
- Pracovní panel: skutečné webové karty Electron WebContentsView, trvalé přihlášení, procházení celého počítače (disky, zpět, vpřed a nahoru), soubory v Monaco editoru, výstupy, Git změny, logy, paměť a artefakty. Šířka se mění myší a ukládá.
- Nabídky: funkční rozbalovací lišty Soubor, Upravit a Zobrazení se zkratkami pro chaty, složky, editaci, panely, prohlížeč, terminál, navigaci, zoom a celou obrazovku.
- Živé kroky: Přijato, Analýza, Plán, Kontext, Provedení, Úpravy, Test, Kontrola a Hotovo/Chyba přes lokální SSE. Průběh je součástí rolovacího chatu, nepřenáší se mezi chaty a po dokončení automaticky zmizí.
- Mozek úloh: před provedením klasifikuje záměr a složitost, sestaví plán, průběžně ukládá kontrolní body, eviduje výsledek každého kroku a po restartu bezpečně obnoví nedokončenou práci. Úlohu označí jako hotovou až po kontrole požadovaných důkazů.
- Bezpečné potvrzení: citlivá akce v režimu Potvrzení používá jednorázové potvrzení svázané s konkrétním chatem, úlohou a přesným příkazem. Potvrzení po deseti minutách vyprší a nelze jej použít podruhé.
- Plánování a pluginy: lokální seznam naplánovaných úkolů a katalog bezplatných modulů.
- Oprávnění: Plný přístup, Potvrzení a Zakázáno; pravidla se vynucují v rozhraní i lokálním backendu.
- Skutečné lokální nástroje: chat umí přes agenty Planner, Files, Tester a Reviewer vytvořit, přečíst a upravit textový soubor, vytvořit složku a obnovitelně odstranit soubor. Výsledek se po provedení zpětně ověřuje.
- Simulace: samostatný přepínač ukáže výsledek lokální akce bez změny počítače. Nevratné a systémové operace vyžadují potvrzení také při Plném přístupu.
- Znalostní knihovna: uživatel v Nastavení vybere jednu nebo více složek či disků, limit velikosti a použití s online AI. Lokální SQLite FTS5 index automaticky dodává relevantní a odtajněný kontext.
- Diagnostika: rychlá kontrola po spuštění a ručně spustitelná úplná kontrola lokálních služeb, modelového serveru, projektu a znalostního indexu.
- Vratné body: lokální snímky projektu s automatickým zachováním nejvýše deseti posledních bodů.

## Soukromí a cena

Raven neaktivuje žádné placené předplatné. Grok a xAI jsou trvale zakázané a OpenRouter používá pouze výslovně schválený model s příponou `:free`. Online poskytovatelé jsou volitelní a používají uživatelem vložené klíče; jejich bezplatné limity a podmínky určuje poskytovatel. Lokální režim běží bez placeného API. Telemetrie aplikace zůstává lokálně ve zvolené instalační složce, například `C:\Raven\runtime`.

## Architektura a inspirace

Rozhraní a návrh pracovních postupů vycházejí z veřejně dostupných principů projektů Open WebUI, OpenCode, Vane, agenticSeek, Meetily a Claw Code. Jejich zdrojové kódy nejsou bez rozmyslu sloučeny do Ravenu; komponenty s nekompatibilní copyleft licencí se připojují pouze přes oddělené rozhraní. Převzaté komponenty a jejich licence jsou uvedeny v [`NOTICE`](NOTICE).

## Vývoj

- Backend: `raven_control.py`
- Desktopové okno: `desktop-electron/main.js`, bezpečný most `desktop-electron/preload.js`
- Rozhraní: `hud/index.html`, `hud/app.css`, `hud/workbench.css`, `hud/hud.js`, `hud/workbench.js`
- Mozek a agentní runtime: `raven_brain.py`, `agent_runtime.py` a `raven_intelligence.py` (plánování, trvalé kontrolní body, ověřování, Pydantic AI Slim, Browser Use, Crawl4AI, MCP; nejvýše dva těžcí agenti)
- Výchozí konfigurace: `defaults/`
- Pravidla pro Codex na každém PC a disku: `AGENTS.md`
- Lokální běhová data: `runtime/`

Před vydáním se kontroluje syntaxe Pythonu, PowerShellu a JavaScriptu, lokální API, souběh agentů, jednorázová potvrzení, zotavení úloh, načtení skutečného Electron rozhraní, psaní do editoru, historie, telemetrie, skutečný souborový nástroj přes chat a obsah instalačního EXE. GitHub se aktualizuje pouze na výslovný pokyn uživatele.

Výsledné soubory jsou `desktop/Raven-Desktop.exe` pro běžné spuštění a `desktop-dist/Raven-1.0-Setup.exe` jako instalační balíček. Instalační EXE obsahuje zdrojovou část Ravenu 1.0 a na čistém podporovaném počítači spustí přípravu bezplatných závislostí.

## Kredity

Instalátor Raven 1.0 stahuje oficiální OpenJarvis jako samostatnou běhovou závislost; jeho zdrojový kód není součástí tohoto repozitáře ani instalačního EXE. Další samostatné open-source knihovny jsou uvedeny v souboru [`NOTICE`](NOTICE). Rozhraní a pracovní postupy byly navrženy také s přihlédnutím k veřejným principům projektů Open WebUI, OpenCode, Vane, Meetily, agenticSeek a Claw Code. Jejich zdrojový kód není součástí Ravenu 1.0 a projekt si nenárokuje jejich značky ani podporu.

## Licence

Raven 1.0 je vydán pod Apache License 2.0. Podrobné kredity a licence integrovaných nebo volitelných komponent jsou v souboru `NOTICE`.
