# Raven 1.0 – opravné a rozšířené sestavení

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

Pro běžnou instalaci stáhněte jediný soubor `Raven-1.0-Setup.exe` z vydání v1.0. Instalátor umožní vybrat cílovou složku, připraví bezplatné závislosti a na konci zkontroluje povinné soubory i spustitelnost aplikace.
