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
- pravidla práce pro Codex jsou uložená přímo v `AGENTS.md`, takže platí také pro přenesenou kopii na jiném PC.

## Ověření sestavení

- kompletní automatické testy Pythonu;
- kontroly syntaxe Pythonu, JavaScriptu a PowerShellu;
- skutečný test zabaleného Electron rozhraní v izolovaném profilu;
- kontrola instalační smlouvy, povinných souborů a sestaveného EXE;
- test lokálního API, oddělení chatů a jednorázového potvrzení včetně odmítnutí opakovaného použití.

## Soubor releasu

Pro běžnou instalaci stáhněte jediný soubor `Raven-1.0-Setup.exe` z vydání v1.0. Instalátor umožní vybrat cílovou složku, připraví bezplatné závislosti a na konci zkontroluje povinné soubory i spustitelnost aplikace.
