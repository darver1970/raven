# Raven 1.2

## Aktualizace Cortex (4. září 2026)

Chat zapisuje cíle, kontrolní body a důkazy do lokální SQLite databáze.
Výběr dostupného lokálního modelu zohledňuje schopnosti, RAM a výsledky testů.
Centrum nabízí pětiúlohový benchmark, zpětnou vazbu a export výslovně schválených
příkladů. Paměť lze vypnout; prošlé zkušenosti se nepoužijí. Export je nutné ručně
zkontrolovat na soukromé údaje; automatická redakce není záruka.

Aktuální pracovní distribuce je kompletní přenosná složka Raven s vlastním
běhovým prostředím. Spouštěč používá relativní cesty, takže kopie není vázaná na
písmeno disku. Instalační EXE se nevytváří ani neupravuje, dokud si jej uživatel
výslovně nevyžádá.

Čistou portable kopii nebo bezpečnou aktualizaci existující kopie připravuje
`prepare-portable.ps1`. Program a modely se překryjí, ale cílové chaty, API klíče,
projekty, cookies, paměť a další osobní data zůstanou oddělená. Cizí DPAPI klíč
zkopírovaný z jiného Windows účtu Raven ignoruje a nezobrazuje jako funkční.

Trénování vah LoRA/QLoRA je odložené. Ukládání zkušeností není trénování vah.
Skóre benchmarku hodnotí pouze uvedené krátké úlohy, nikoli obecnou inteligenci.
Plný autonomní vykonavatel plánů, univerzální sandbox a automatický rollback mozku
nejsou dokončené funkce tohoto sestavení. Záznam plánu není důkaz jeho provedení.

Raven 1.2 je lokální desktopový AI pracovní prostor pro 64bitové Windows 10 a Windows 11. Nové Centrum schopností sjednocuje správu lokálních modelů, výkonové profily, znalosti, typovanou paměť, bezpečné MCP registry, workflow, soukromí, kontext, podporu a experimentální funkce. Stabilní režim zůstává lehký a local-first.

Online AI je volitelná. Automatický router používá pouze lokální Ollamu, dokud uživatel v nastavení nepotvrdí odesílání obsahu online poskytovatelům. Raven nikdy automaticky neaktivuje placený model ani předplatné.

## Nejjednodušší spuštění

1. Připojte připravený portable disk nebo zkopírujte celou složku Raven.
2. Vedle složky spusťte `Raven Portable.exe`; grafický spouštěč si kořen odvodí ze svého aktuálního umístění a není vázaný na písmeno disku.
3. Nepřesouvejte jednotlivé soubory mimo portable celek. Python, Node/Electron, Ollama, modely a runtime musí zůstat v domluveném rozložení.

Raven nikdy neaktivuje placené předplatné ani placené API. Volitelný Codex lze spustit jen ručně, pokud k němu uživatel již má vlastní přístup; automatické směrování ho nepoužívá.

### Požadavky

- 64bitové Windows 10 nebo Windows 11;
- alespoň 24 GB volného místa;
- připravená kompletní portable distribuce obsahující vlastní Python, Node/Electron, Ollamu a modely;
- internet je potřeba pouze pro volitelné online poskytovatele a aktualizace;
- Microsoft Visual C++ Runtime 2015–2022 x64 pro nativní Python komponenty;
- pro lokální AI je doporučeno nejméně 16 GB RAM, základní cloudový režim může fungovat i na slabším PC.

### Vývojová příprava ze zdrojů

Zdrojový repozitář neobsahuje modely ani kompletní portable runtime. Pro vývoj lze po stažení zdrojů spustit:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -InstallPath C:\Raven
```

Skript připraví vývojové závislosti a desktopovou vrstvu. Výchozí uživatelskou distribucí je kompletní portable složka; instalační EXE není součástí vydání 1.2.

### Aktualizace

Portable Raven kontroluje nejnovější veřejné GitHub Release. Stahuje pouze `raven-portable-update.json` a jím popsaný archiv, kontroluje původ, velikost, SHA-256 archivu i jednotlivých souborů a při instalaci zachová vlastní `runtime`, chaty, klíče, projekty, paměť i modely. API klíče a lokální historie se do GitHubu nikdy nenahrávají.

## Modely a automatické přepínání

Výchozí automatický režim používá local-first pořadí, které lze v nastavení změnit:

1. lokální Ollama;
2. Groq Free, Gemini Free a Cerebras Free;
3. OpenRouter Free, Mistral Free, GitHub Models a volitelně Cloudflare Workers AI.

Na další zdroj se přepne při vyčerpání bezplatné kvóty nebo při nedostupnosti služby. API klíče se ukládají šifrovaně přes Windows DPAPI a nejsou součástí repozitáře ani historie chatu. Ručně lze zvolit režim Automaticky, Lokálně, Rychlost, Kvalita, Výzkum nebo Kód.

## Hlavní části

- Centrum schopností 1.2: přehled nejméně 32 oblastí, skutečný stav hardwaru, bezpečný a offline režim, experimentální laboratoř a přístupnost.
- Správce modelů: skutečný katalog Ollamy, velikosti modelů, doporučení podle RAM, instalace, odstranění a lokální rychlostní test s potvrzením.
- Bezpečné registry: MCP servery bez ukládání jejich tajemství, explicitní oprávnění, kontrolované workflow, verzované prompty a typovaná paměť s deduplikací a expirací.
- Soukromí: lokální audit metadat online přenosů, redakce prompt injection, export nastavení bez DPAPI klíčů a anonymizovaný report podpory.
- Izolace: omezená pracovní kopie bez `.git`, runtime, modelů, závislostí a uživatelských tajemství; experimentální funkce jsou standardně vypnuté.

- Chat: víceřádkový editor, Enter pro odeslání a Shift+Enter pro nový řádek, lokální historie, kopírování odpovědí a bloků kódu. Vlastní zprávu lze upravit a odpověď znovu vytvořit z opravené historie. Během probíhající práce lze připnout, upravit nebo zrušit navazující usměrnění. 👍/👎 je trvalá zpětná vazba ke konkrétní odpovědi a změna hodnocení starý záznam nahradí.
- Projekty: oddělené pracovní kontexty s cestou, Git repozitářem, technologiemi, poznámkami a testovacím příkazem. Dialog lze zavřít i s prázdným názvem a každý projekt včetně aktivního lze odstranit ze seznamu bez smazání jeho souborů.
- Agenti: větve Core, Planning, Research, Browser, Coding, Testing, Files, Memory, Security a System; výchozí Analytik s lokálním Qwen 3.5 9B, přidávání vlastních agentů, závislosti, průběh, živé stavy a samostatné okno.
- Systém: stabilní telemetrie bez problikávání, živé grafy CPU/RAM/GPU/disku/sítě, teploty, příkon, ventilátory, upozornění, nejzatíženější procesy a samostatný bar zaplnění každého disku. CPU, RAM, disky, síť a procesy fungují přes nativní Windows/psutil i bez administrátorských senzorů; teploty zůstávají samostatně volitelné. Procesy jsou seskupené jako ve Správci úloh a lze je filtrovat a řadit podle komponent.
- Ovládání počítače: seznam a aktivace oken, snímání plochy, dostupný strom Windows UI Automation a po potvrzení myš a klávesnice. Sekvence je svázaná s cílovým oknem, kontroluje souřadnice, pořizuje důkaz před/po a zapisuje audit bez obsahu psaného textu. Chráněná okna jsou blokovaná a k dispozici je nouzové zastavení.
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
- Poskytovatelé AI: oficiální odkazy pro získání klíče, textový stav, poslední úspěšný test, průměrná odezva, společný test uložených klíčů a samostatný Cloudflare Account ID. Volitelný ruční Codex používá oficiální přihlášení ChatGPT, nikoli API klíč, a není součástí automatického směrování.
- Zobrazení: trvale uložené měřítko 75–150 %, tlačítka, posuvník, přizpůsobení oknu a zkratky Ctrl +, Ctrl − a Ctrl 0. Při velkém měřítku na užším displeji se pracovní panel automaticky sbalí, aby se obsah nepřekrýval.
- Vratné body: lokální snímky projektu s automatickým zachováním nejvýše deseti posledních bodů.

## Soukromí a cena

Raven neaktivuje žádné předplatné, nákup ani placené API. Automatický router používá jen bezplatné kvóty a lokální modely a nikdy sám nekoupí kredity. Volitelný Codex je dostupný pouze ručně uživateli, který k němu již má vlastní přístup. Grok a xAI jsou trvale zakázané a OpenRouter používá pouze výslovně schválený bezplatný model. Online poskytovatelé jsou volitelní a používají uživatelem vložené klíče; jejich bezplatné limity a podmínky určuje poskytovatel. Telemetrie aplikace zůstává lokálně ve zvolené instalační složce, například `C:\Raven\runtime`.

## Architektura a inspirace

Rozhraní a návrh pracovních postupů vycházejí z veřejně dostupných principů projektů Open WebUI, OpenCode, Vane, agenticSeek, Meetily a Claw Code. Jejich zdrojové kódy nejsou bez rozmyslu sloučeny do Ravenu; komponenty s nekompatibilní copyleft licencí se připojují pouze přes oddělené rozhraní. Převzaté komponenty a jejich licence jsou uvedeny v [`NOTICE`](NOTICE).

## Vývoj

- Backend: `raven_control.py`
- Desktopové okno: `desktop-electron/main.js`, bezpečný most `desktop-electron/preload.js`
- Rozhraní: `hud/index.html`, `hud/app.css`, `hud/workbench.css`, `hud/hud.js`, `hud/workbench.js`
- Mozek a agentní runtime: `raven_brain.py`, `agent_runtime.py`, `raven_intelligence.py` a `raven_next.py` (plánování, checkpointy, znalosti, bezpečnostní registry a Centrum schopností; nejvýše dva těžcí agenti)
- Výchozí konfigurace: `defaults/`
- Pravidla pro Codex na každém PC a disku: `AGENTS.md`
- Lokální běhová data: `runtime/`

Před vydáním se kontroluje syntaxe Pythonu, PowerShellu a JavaScriptu, lokální API, souběh agentů, jednorázová potvrzení, zotavení úloh, načtení skutečného Electron rozhraní, psaní do editoru, historie, telemetrie, skutečný souborový nástroj přes chat, lokální odpověď modelu a čisté ukončení všech vlastních procesů. Raven si eviduje přesný proces své Ollamy a při ukončení nesmí zastavit cizí instanci. GitHub se aktualizuje pouze na výslovný pokyn uživatele.

Aktuální lokální ověření Raven 1.2 ze dne 12. září 2026 zahrnuje 231 úspěšných automatických testů a pět záměrně přeskočených volitelných živých scénářů. Samostatně prošel živý lokální AI planner, celé HTTP → Cortex → ovládání Windows → ověření → audit, rozšířený Electron smoke test a ostrý start z C: i z hlavní portable flash. Finální flash proces běžel přímo ze stabilní cesty `Raven-1.2\desktop\Raven-Desktop.exe` a měl viditelné reagující okno.

Výsledkem je přímý `desktop/Raven-Desktop.exe` uvnitř kompletní portable složky a kořenový grafický spouštěč `Raven Portable.exe`. Pro autoupdate release obsahuje `raven-portable-update.json` a odpovídající ověřený ZIP. Instalační EXE se nevytváří, dokud o něj uživatel výslovně nepožádá.

## Kredity

Raven používá OpenJarvis jako samostatnou běhovou závislost; jeho zdrojový kód není součástí tohoto repozitáře. Další samostatné open-source knihovny jsou uvedeny v souboru [`NOTICE`](NOTICE). Rozhraní a pracovní postupy byly navrženy také s přihlédnutím k veřejným principům projektů Open WebUI, Jan, AnythingLLM, OpenHands, Browser Use, Mem0, Dify, OpenCode, Vane, Meetily, agenticSeek a Claw Code. Jejich zdrojový kód není automaticky kopírován do Ravenu a projekt si nenárokuje jejich značky ani podporu.

## Licence

Raven 1.2 je vydán pod Apache License 2.0. Podrobné kredity a licence integrovaných nebo volitelných komponent jsou v souboru `NOTICE`.
