// Dedicated command-file entry point. Runs on the world thread inside CheckCommand.
using System;
using System.Linq;
using Keen.Game2.Simulation.GameSystems.Contracts;
using Keen.Game2.Simulation.GameSystems.Encounters;
using Keen.Game2.Simulation.GameSystems.Ownership;
using Keen.Game2.Simulation.WorldObjects.CubeBlocks;
using Keen.Game2.Simulation.WorldObjects.Items;
using Keen.VRage.Core;
using Keen.VRage.Core.Game.Components;
using Keen.VRage.Core.Game.Data;
using Keen.VRage.Core.Game.Systems;
using Keen.VRage.DCS.Components;
using Keen.VRage.Library.Localization;
using Keen.VRage.Library.Utils;
using Y51_SE2Online.NetHost;
using Y51_SE2Online.Sync;
using Y51_SE2Online.SyncEngine;

namespace Y51_SE2Online.Dedicated;

internal static class ContractAdmin
{
    private static void Log(string text) => GameLog.Instance.Info("console: [contracts] " + text);

    public static bool TryRun(string command)
    {
        var args = command.Split(' ', 4, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        if (args.Length == 0 || !args[0].Equals("contract", StringComparison.OrdinalIgnoreCase)) return false;
        bool list = args.Length >= 2 && args[1].Equals("list", StringComparison.OrdinalIgnoreCase);
        bool complete = args.Length >= 2 && args[1].Equals("complete", StringComparison.OrdinalIgnoreCase);
        if (!(list && args.Length >= 3 || complete && args.Length == 4))
        {
            Log("contract list <SteamID lub nick> [szukany tekst] | contract complete <SteamID lub nick> <ID lub nazwa>");
            return true;
        }

        var host = NetSession.Current?.Host;
        var session = MpPlugin.App?.ServerSession;
        var contracts = session?.SessionComponents.TryGet<ContractSessionComponent>();
        if (host == null || contracts == null) throw new InvalidOperationException("Świat lub system kontraktów nie jest gotowy.");
        ulong steam = host.FindPlayer(args[2]);
        if (steam == 0) throw new ArgumentException("Nie rozpoznano gracza. Podaj jego SteamID64.");
        var account = host.AccountInfo(steam);
        if (complete && !ulong.TryParse(args[2], out _) && !string.Equals(args[2], account.Name, StringComparison.OrdinalIgnoreCase))
            throw new ArgumentException("Przy zaliczaniu podaj dokładny nick albo SteamID64.");
        var identity = new IdentityId(account.Identity);
        var players = contracts._identities.GetPlayerIdentityIds().ToArray();
        if (account.Identity == 0 || !players.Contains(identity)) throw new ArgumentException("Gracz nie ma tożsamości w tym świecie.");

        string query = args.Length == 4 ? args[3] : "";
        bool number = ulong.TryParse(query, out _);
        bool beacon = query.Equals("radiolatarnia", StringComparison.OrdinalIgnoreCase) || query.Equals("beacon", StringComparison.OrdinalIgnoreCase);
        var hits = contracts.GetContractIds().Select(id =>
        {
            var def = contracts.GetContractData(id).Definition;
            string label = def.Title.ToString();
            try { label = def.Title.EvaluateText(); } catch { /* A headless host may have no translation. */ }
            string sector = contracts.TryGetSectorByContract(id)?.Name ?? "?";
            return (id, text: $"{label} | {def.DebugNameShort} | {sector}");
        }).Where(row => number ? row.id.ToString() == query : (row.text.Contains(query, StringComparison.OrdinalIgnoreCase) ||
            beacon && (row.text.Contains("beacon", StringComparison.OrdinalIgnoreCase) || row.text.Contains("radiolatarnia", StringComparison.OrdinalIgnoreCase)))).ToArray();
        string Status(IdentityId who, ContractId id)
        {
            var state = contracts.GetContractsStateFor(who);
            if (state.CompletedContracts.Contains(id)) return "ukończony";
            if (state.FinishedContracts.Contains(id)) return "do odbioru";
            if (state.AcceptedContracts.Contains(id)) return "aktywny";
            if (state.FailedContracts.Any(f => f.Contract == id)) return "nieudany";
            return "nieprzyjęty";
        }

        if (list || hits.Length != 1)
        {
            foreach (var row in hits) Log($"{row.id} | {Status(identity, row.id)} | {row.text}");
            if (hits.Length == 0) Log("Brak wyników. Użyj contract list z samym nickiem, aby zobaczyć nazwy i ID z tego świata.");
            else if (!list) Log("Pasuje kilka kontraktów. Wybierz jeden po ID.");
            return true;
        }

        var id = hits[0].id;
        string status = Status(identity, id);
        if (status == "ukończony") { Log($"Kontrakt {id} już ukończony. Nagrody nie są ponownie wypłacane."); return true; }
        if (HostWorld.HostContracts is not (ResearchMode.Own or ResearchMode.Faction))
            throw new InvalidOperationException("Naprawa wymaga contracts=individual lub faction. W trybie everyone postęp jest globalny.");
        if (contracts.GetContractData(id).Endless || contracts._contractPendingRemoval.ContainsKey(id))
            throw new InvalidOperationException("Ta naprawa dotyczy jednorazowych kontraktów, które nadal istnieją w świecie.");
        if (status == "nieudany" || (status == "nieprzyjęty" && contracts.IsLocked(identity, id)))
            throw new InvalidOperationException("Kontrakt jest zablokowany lub nieudany.");
        using var sharing = contracts._storage.GetSharingIdentities(identity, id, ContractStateChange.Completed);
        var recipients = sharing.Data.ToArray();
        if (!recipients.Contains(identity) || recipients.Any(p => Status(p, id) != status))
            throw new InvalidOperationException("Członkowie grupy mają różne stany tego kontraktu. Przerwano przed wypłatą.");
        if (players.Except(recipients).Any(p => Status(p, id) is "aktywny" or "do odbioru"))
            throw new InvalidOperationException("Inny gracz lub frakcja ma ten kontrakt aktywny albo do odbioru.");
        // FinishContract acts on every accepting identity. The checks above restrict its effect to this group.
        if (!contracts._contractToBlock.TryGetValue(id, out var provider))
            throw new InvalidOperationException("Brak terminala przypisanego do tego kontraktu.");
        var terminals = session.GetEntitiesOfType<ContractBlockComponent>()
            .Select(entity => entity.Get<ContractBlockComponent>())
            .Where(block => block.Id == provider && block.Entity.Components.OfType<InventoryComponent>().Any()).ToArray();
        if (terminals.Length != 1)
            throw new InvalidOperationException("Podejdź do terminala wystawiającego kontrakt, aby gra go wczytała. Wymagany jeden terminal z magazynem.");
        var terminal = terminals[0];
        if (status == "aktywny")
        {
            using var onFinish = contracts._storage.GetSharingIdentities(identity, id, ContractStateChange.Finished);
            if (!onFinish.Data.ToArray().ToHashSet().SetEquals(recipients))
                throw new InvalidOperationException("Różne grupy odbiorców przy zakończeniu i odbiorze kontraktu.");
            contracts.FinishContract(id);
            if (recipients.Any(p => Status(p, id) != "do odbioru"))
                throw new InvalidOperationException("Gra nie potwierdziła zakończenia dla całej grupy. Sprawdź contract list.");
        }
        if (!contracts.TryCompleteContract(identity, terminal, id))
            throw new InvalidOperationException("Gra odmówiła odbioru przy tym terminalu. Sprawdź contract list.");
        if (recipients.Any(p => Status(p, id) != "ukończony"))
            throw new InvalidOperationException("Gra nie potwierdziła odbioru przez całą grupę. Sprawdź stan przed ponowieniem.");
        Log($"Ukończono {id} | {hits[0].text} | SteamID {steam} | odbiorców {recipients.Length}. Gra przyznała standardowe nagrody.");
        DedicatedRuntime.SaveNow(ok => Log(ok ? $"Zapisano świat po ukończeniu {id}." : "Świat jeszcze niezapisany. Użyj save i sprawdź potwierdzenie w konsoli."));
        return true;
    }
}
