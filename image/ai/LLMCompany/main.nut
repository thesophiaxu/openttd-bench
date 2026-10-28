// LLMCompany: owns a company slot for an LLM seat and provides the one capability a
// GameScript lacks — AIOrder writes. The GameScript enqueues each order edit as a
// sign named `LLM:<op>:<vehicle>:<args...>` (owned by this company, so rivals can
// neither see nor rename it); this AI applies it and renames the sign `LLMR:<1|0>`.
// AI-only native events are relayed the same way (`LLME:<type>:<vehicle>`).

class LLMCompany extends AIController {
    function Save() { return { version = 5 }; }
    function Load(version, data) { }

    function Split(value) {
        local parts = [];
        local start = 0;
        for (local i = 0; i < value.len(); i++) {
            if (value.slice(i, i + 1) != ":") continue;
            parts.append(value.slice(start, i));
            start = i + 1;
        }
        parts.append(value.slice(start));
        return parts;
    }

    function Apply(fields) {
        // fields: ["LLM", op, vehicle, ...]
        if (fields.len() < 3) return "malformed";
        local op = fields[1];
        local vehicle = fields[2].tointeger();
        if (!AIVehicle.IsValidVehicle(vehicle)) return "no_such_vehicle";
        local ok = false;
        switch (op) {
            case "A": if (fields.len() != 5) return "malformed";
                ok = AIOrder.AppendOrder(vehicle, fields[3].tointeger(), fields[4].tointeger()); break;
            case "D": if (fields.len() != 5) return "malformed";
                ok = AIOrder.AppendOrder(vehicle, fields[3].tointeger(), fields[4].tointeger()); break;
            case "I": if (fields.len() != 6) return "malformed";
                ok = AIOrder.InsertOrder(vehicle, fields[3].tointeger(), fields[4].tointeger(), fields[5].tointeger()); break;
            case "R": if (fields.len() != 4) return "malformed";
                ok = AIOrder.RemoveOrder(vehicle, fields[3].tointeger()); break;
            case "F": if (fields.len() != 5) return "malformed";
                ok = AIOrder.SetOrderFlags(vehicle, fields[3].tointeger(), fields[4].tointeger()); break;
            case "K": if (fields.len() != 4) return "malformed";
                ok = AIOrder.SkipToOrder(vehicle, fields[3].tointeger()); break;
            case "C": if (fields.len() != 4) return "malformed";
                ok = AIOrder.CopyOrders(vehicle, fields[3].tointeger()); break;
            case "H": if (fields.len() != 4) return "malformed";
                ok = AIOrder.ShareOrders(vehicle, fields[3].tointeger()); break;
            case "U": ok = AIOrder.UnshareOrders(vehicle); break;
            default: return "unknown_op";
        }
        if (ok) return null;
        local err = AIError.GetLastErrorString();
        if (err == null) err = "failed";
        // Keep the ack within the 31-char sign limit and free of separators.
        local clean = "";
        for (local i = 0; i < err.len() && clean.len() < 20; i++) {
            local ch = err.slice(i, i + 1);
            if (ch == ":" || ch == " ") ch = "_";
            clean += ch;
        }
        return clean;
    }

    function DrainMailbox() {
        local signs = AISignList();
        signs.Sort(AIList.SORT_BY_ITEM, true);  // apply tickets in creation order
        foreach (sign, _ in signs) {
            local name = AISign.GetName(sign);
            if (name == null || name.len() < 5 || name.slice(0, 4) != "LLM:") continue;
            local err = this.Apply(this.Split(name));
            local ack = err == null ? "LLMR:1" : ("LLMR:0:" + err);
            AILog.Warning("mailbox " + name + " -> " + ack);
            if (!AISign.SetName(sign, ack)) {
                AILog.Error("could not acknowledge mailbox item " + name);
                AISign.RemoveSign(sign);
            }
        }
    }

    function Relay(type, vehicle) {
        local tile = AICompany.GetCompanyHQ(AICompany.COMPANY_SELF);
        if (tile == AIMap.TILE_INVALID) {
            if (vehicle >= 0 && AIVehicle.IsValidVehicle(vehicle)) tile = AIVehicle.GetLocation(vehicle);
            else tile = AIMap.GetTileIndex(1, 1);
        }
        AISign.BuildSign(tile, "LLME:" + type + ":" + vehicle);
    }

    function DrainEvents() {
        local n = 0;
        while (AIEventController.IsEventWaiting() && n++ < 50) {
            local ev = AIEventController.GetNextEvent();
            switch (ev.GetEventType()) {
                case AIEvent.ET_VEHICLE_LOST:
                    this.Relay("vehicle_lost", AIEventVehicleLost.Convert(ev).GetVehicleID()); break;
                case AIEvent.ET_VEHICLE_WAITING_IN_DEPOT:
                    this.Relay("vehicle_waiting_in_depot", AIEventVehicleWaitingInDepot.Convert(ev).GetVehicleID()); break;
                case AIEvent.ET_VEHICLE_UNPROFITABLE:
                    this.Relay("vehicle_unprofitable", AIEventVehicleUnprofitable.Convert(ev).GetVehicleID()); break;
                case AIEvent.ET_STATION_FIRST_VEHICLE:
                    this.Relay("station_first_vehicle", AIEventStationFirstVehicle.Convert(ev).GetVehicleID()); break;
                case AIEvent.ET_AIRCRAFT_DEST_TOO_FAR:
                    this.Relay("aircraft_dest_too_far", AIEventAircraftDestTooFar.Convert(ev).GetVehicleID()); break;
                default: break;
            }
        }
    }

    function Start() {
        AILog.Warning("LLMCompany v5 holding company " + AICompany.ResolveCompanyID(AICompany.COMPANY_SELF));
        while (true) {
            this.DrainEvents();
            this.DrainMailbox();
            this.Sleep(1);
        }
    }
}
