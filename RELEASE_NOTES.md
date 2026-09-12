# Raven 1.2 – centrum lokálních schopností

## Opravná verze 1.2.1 — 12. září 2026

- Doplněny nativně dostupné údaje telemetrie: CPU takt, fyzická a logická jádra, celková RAM, stav baterie a napájení, doba běhu a síťový provoz.
- Disky se zobrazují nezávisle na volitelném LibreHardwareMonitoru. Chybějící teploty už mají konkrétní vysvětlení a lze spustit přibalené senzory jako správce přes standardní UAC.
- Opraveny tři aktuální skupiny npm zranitelností; produkční i úplný audit mají 0 známých zranitelností.
- Zrychlena budoucí příprava portable kopie vyloučením nepoužívaných vývojových npm balíků z flash distribuce.
- Ověření před vydáním: `234 passed, 5 skipped`, živý Electron HUD, živé ovládání PC ve třech vrstvách, lokální Builder, omezený-PATH portable test na C: i E: a přímý viditelný start z E:.

## Finální portable opravy — 12. září 2026

- Produkční ovládání počítače je napojené na Cortex a dokončení vyžaduje ověřený důkaz; UI Automation používá stabilní COM vlákno.
- Telemetrie bez správce poskytuje skutečné CPU, RAM, procesové GPU, disky a procesy; nedostupné teploty se nevymýšlejí.
- Opraveny dialogy Projekt, Agent a Paměť, úpravy uživatelských zpráv, trvalé 👍/👎, steering probíhající práce a klávesa Escape v paletě příkazů.
- Opraveno desktopové balení: portable složka používá přímý proces `desktop/Raven-Desktop.exe`, nikoli vnořený samorozbalovací obal.
- Automatické testy bez nepoužívaného instalátoru: `231 passed, 5 skipped`; rozšířený Electron smoke test a ostrý start z C: i flash prošly.
- Aktualizace stahuje podepsaný obsahem manifestovaný portable overlay a zachovává runtime, chaty, API klíče, projekty, paměť a modely.

## Aktuální portable opravy — 5. září 2026

- Přidáno cílené ovládání Windows: snímky obrazovky, seznam a aktivace oken, UI Automation, myš, klávesnice, audit před/po, ochrana citlivých oken a nouzové zastavení.
- Základní telemetrie CPU, RAM, disků, sítě a procesů funguje i bez LibreHardwareMonitoru; nedostupnost teplotních senzorů už nevypne celý přehled.
- Opraven dialog nového projektu a přidáno odstranění libovolného projektu ze seznamu bez mazání jeho souborů na disku.
- Uživatelské zprávy lze upravit a odpověď znovu vytvořit; během práce lze připnout, upravit nebo zrušit navazující usměrnění.
- 👍/👎 se trvale váže ke konkrétní odpovědi a změna hodnocení nahrazuje předchozí záznam.
- Přidán `prepare-portable.ps1`: čistá kopie nepřenáší chaty, klíče, projekty, cookies ani osobní databáze; aktualizace zachová vlastní data cílové portable kopie. Cizí DPAPI klíče už nejsou hlášeny jako nastavené.
- Ověření: 164 testů prošlo, 3 volitelné živé testy přeskočeny; živé ovládání okna 7/7 i živé plánování lokálním modelem prošlo; Electron smoke a portable end-to-end prošly.
- Instalační EXE nebylo v tomto kroku sestaveno, upraveno ani vydáno. Výchozí distribucí zůstává kompletní portable složka.

## Cortex — pracovní aktualizace 2026-09-04

- Opraven převod textové důvěry kontrolora v novém HTTP chatu.
- Přidány lokální zkušenosti, zpětná vazba, export schválených dat a měření kontraktů odpovědí.
- Doplněny nové moduly do instalačního kopírování i kontroly.
- Opraveno ukončování backendu spuštěného přes `python -m openjarvis.cli serve`.
- Trénink vah je odložený. Plná autonomní obnova a rollback zůstávají nedokončené.

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

## Portable distribuce 1.2

Výchozí distribucí je kompletní portable složka s kořenovým `Raven Portable.exe`. GitHub release poskytuje ověřený programový overlay `Raven-Portable-Update-v1.2.1.zip` a manifest `raven-portable-update.json` pro vestavěný autoupdate. Instalační EXE není součástí tohoto vydání.

## Ověření portable balíku 1.2

- 231 automatických testů prošlo a pět volitelných živých testů bylo záměrně přeskočeno;
- produkční kontrola npm nenašla žádnou známou zranitelnost;
- portable HUD, terminál a souborový agent prošly skutečným testem vytvoření, přečtení a odstranění souboru;
- aktualizace zachovává celý uživatelský `runtime` a odmítá archiv s neplatnou cestou, velikostí nebo kontrolním součtem;
- přenosná kopie prošla z `C:` i z flashdisku, opravila si změněné písmeno Python cest a po ukončení nezanechala vlastní proces ani port;
- lokální model na PC i flashdisku odpověděl na kontrolní úlohu `42` a kontrolor výsledek přijal;
- instalace modelů byla v opakovaném čistém QA běhu přeskočena, protože stejné lokální modely a jejich kontrolní součty byly ověřeny v přenosné kopii;
- čistý instalační test proběhl v izolované složce tohoto Windows počítače, nikoli v samostatném virtuálním počítači.

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
