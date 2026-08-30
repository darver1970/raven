# Raven 1.2 – centrum lokálních schopností

## Novinky 1.2

- nové Centrum schopností sjednocuje správu modelů, výkonu, bezpečnosti, soukromí, MCP, workflow, paměti, promptů a experimentů;
- Správce modelů čte skutečný stav Ollamy, velikosti modelů a doporučení podle hardwaru a po potvrzení umí model nainstalovat, otestovat nebo odstranit;
- bezpečný a offline režim jsou vynucené přímo backendem a znemožní odeslat požadavek online;
- MCP registry vyžadují explicitní oprávnění, nové servery ukládají vypnuté a neukládají jejich tajné proměnné;
- typovaná paměť podporuje rozsah, expiraci, připnutí a slučování duplicit;
- znalostní knihovna indexuje přírůstkově a výsledky obsahují citaci souboru a řádku;
- ochrana proti prompt injection označí a nahradí nedůvěryhodné instrukce před online přenosem;
- lokální privacy audit ukládá pouze metadata, nikdy obsah zpráv nebo API klíče;
- export nastavení záměrně vynechává DPAPI klíče, cache a modely;
- izolované pracovní kopie vynechávají Git, runtime, modely, závislosti a velké soubory;
- přidány výkonnostní profily, rozpočet kontextu, vysoký kontrast, omezení animací a jednodušší režim;
- stabilní funkce jsou aktivní, zatímco multimodalita, dokumenty, LAN API a další rizikovější části zůstávají v experimentální laboratoři standardně vypnuté;
- rozšířená automatická sada ověřuje bezpečnost registrů, export bez tajemství, izolaci, přírůstkový index, citace a celé Electron rozhraní.

## Instalace 1.2

Stáhněte `Raven-1.2-Setup.exe` z vydání v1.2. Pro automatickou aktualizaci musí vydání obsahovat také odpovídající `latest.yml` a blockmapu.

## Předchozí sestavení 1.1

# Raven 1.1 – bezpečný katalog bezplatných AI

## Novinky 1.1

- nastavení zobrazuje bezplatné limity, modality a upozornění na soukromí u každého podporovaného poskytovatele;
- uživatel může měnit přesné pořadí automatických fallbacků; výchozí pořadí začíná lokální Ollamou;
- online poskytovatelé se v automatickém režimu nepoužijí, dokud uživatel výslovně nepotvrdí, že obsah konverzace opustí počítač;
- Raven nikdy automaticky nepřejde na placený model ani neprovede nákup;
- API klíče zůstávají šifrované Windows DPAPI a nejsou součástí nastavení, logů, instalačního balíčku ani GitHubu;
- chyby kvóty, dočasné výpadky, opakování a lokální fallback zůstávají pokryté automatickými testy.

## Předchozí sestavení 1.0

## Hlavní změny

- kompletní přejmenování aplikace, HUD, EXE a instalátoru na Raven 1.0;
- nové originální logo Raven;
- nabídky Soubor, Upravit a Zobrazení v nejvyšší titulkové liště;
- pracovní průběh pouze uvnitř rolovacího chatu, oddělený pro každý chat a automaticky skrytý po dokončení;
- informace o změněných souborech se nezobrazuje kvůli starému stavu Git projektu, lze ji zavřít a sama mizí;
- výchozí agent Analytik s bezplatným lokálním modelem Qwen 3.5 9B;
- zpřesněná karta Rozšíření pro volitelné agenty;
- aktualizované README, licence a kredity OpenJarvisu jako samostatné běhové závislosti;
- instalační program umožňuje vybrat cílovou složku a používá pouze bezplatné komponenty.
- nový mozek úloh s klasifikací záměru, plánem, trvalými kontrolními body a bezpečným obnovením po restartu;
- povinné důkazy a závěrečná kontrola brání tomu, aby Raven nepravdivě oznámil dokončení;
- jednorázová potvrzení jsou svázaná s konkrétním chatem, úlohou a příkazem, po deseti minutách vyprší a nelze je opakovat;
- strukturovaný a seřazený živý průběh práce se zobrazuje pouze ve správném chatu;
- opravený globální limit souběžných těžkých agentů i při souběžných HTTP požadavcích;
- instalátor kopíruje a před dokončením také syntakticky ověří nové jádro `raven_brain.py`;
- licence zůstává Apache License 2.0, kredity a samostatné závislosti byly doplněny v `NOTICE`.
- pravidla práce pro Codex jsou uložená přímo v `AGENTS.md`, takže platí také pro přenesenou kopii na jiném PC;
- instalátor připraví tři bezplatné lokální modely `qwen3.5:4b`, `qwen3.5:9b` a `qwen2.5-coder:7b` přímo v instalační složce;
- vlastní Ollama je svázaná s ověřeným PID, spouštěcím časem a instalačním kořenem; zavření Ravenu ukončí celý její procesový strom, ale nedotkne se cizí Ollamy;
- NSIS instalace používá zabalený Electron shell a na cílovém počítači znovu nesestavuje desktopovou aplikaci.
- opraveno rozpoznání instalační cesty končící názvem `Desktop`; nainstalovaná NSIS aplikace se již nezamění za přenosné EXE a neposune kořen projektu o složku výš;
- přidán spustitelný regresní test pro NSIS cestu na ploše a přenosnou kopii v adresáři `desktop`.
- při dlouhé kompilaci nativních částí OpenJarvisu instalátor výslovně upozorní, že na pomalejším PC může několik minut pokračovat bez dalšího výpisu.
- souběžné požadavky na ukončení jsou serializované, aby při zavření okna nezůstal běžet Ravenem spuštěný proces Ollama.
- vlastnictví procesu Ollama porovnává čas spuštění stejným Windows API jako launcher; kontrolu již nerozhodí posun místního časového pásma.

## Ověření sestavení

- kompletní automatické testy Pythonu;
- kontroly syntaxe Pythonu, JavaScriptu a PowerShellu;
- skutečný test zabaleného Electron rozhraní v izolovaném profilu;
- kontrola instalační smlouvy, povinných souborů a sestaveného EXE;
- test lokálního API, oddělení chatů a jednorázového potvrzení včetně odmítnutí opakovaného použití;
- čistá instalace do nové složky včetně závislostí, tří modelů a závěrečného ověřovacího souboru;
- spuštění nainstalovaného `Raven.exe`, načtení HUD, terminál, agentní strom, telemetrie, vytvoření a odstranění souboru přes chat a skutečná lokální odpověď `42`;
- zavření nainstalované aplikace s výsledkem nula vlastních procesů a nula naslouchajících portů Raven.
- skutečná instalace a bootstrap v cestě s mezerami a diakritikou a regresní test cíle končícího `Desktop`.

## Soubor releasu

Pro běžnou instalaci stáhněte jediný soubor `Raven-1.1-Setup.exe` z vydání v1.1. Instalátor umožní vybrat cílovou složku, připraví bezplatné závislosti a na konci zkontroluje povinné soubory i spustitelnost aplikace.
