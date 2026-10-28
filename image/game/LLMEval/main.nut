// LLMEval: a deity GameScript exposing goal-free primitives to the control plane.
// It awaits a `configure` admin message, then loops: drain admin events (batches),
// fund companies as they appear, take per-day measurements, sweep order tickets.
// It has no target, no deadline and no notion of success.

require("lib/util.nut");
require("lib/dispatch.nut");
require("profit.nut");
require("meter.nut");
require("sys.nut");
require("inspect/tiles.nut");
require("inspect/towns.nut");
require("inspect/industries.nut");
require("inspect/stations.nut");
require("inspect/companies.nut");
require("inspect/vehicles.nut");
require("inspect/cargos.nut");
require("land.nut");
require("rail.nut");
require("road.nut");
require("vehicles.nut");
require("orders.nut");

class LLMEval extends GSController {
    configured = false;
    ticks = 0;

    function Save() { return { version = 4, meter = Meter.Save() }; }
    function Load(version, data) {
        if (!("version" in data) || data.version != 4 || !("meter" in data)) {
            throw "legacy save lacks disposal-safe accounting; start a fresh episode";
        }
        Meter.Load(data.meter);
        this.configured = Meter.config != null;
    }

    function Start() {
        Util.log_level = GSController.GetSetting("log_level");
        Util.Info("LLMEval v3 started; " + Registry.names.len() + " commands registered; awaiting configure");
        Util.Send({ push = "ready", commands = Registry.names.len() });
        while (true) {
            this.DrainEvents();
            if (this.configured) {
                Meter.FundCompanies();
                Meter.Tick();
                Orders.Sweep();
                Orders.SweepEvents();
            } else if (this.ticks % 40 == 0) {
                // The admin may connect after Start(): keep announcing until configured.
                Util.Send({ push = "ready", commands = Registry.names.len(), tick = GSController.GetTick() });
            }
            this.ticks++;
            this.Sleep(1);
        }
    }

    function Configure(request) {
        local cfg = request.configure;
        Meter.Configure(cfg);
        this.configured = true;
        Util.Info("configured: " + (("llm_seats" in cfg) ? cfg.llm_seats : 1) + " llm seats");
        Util.Send({ id = ("id" in request) ? ("" + request.id) : "configure", ok = true,
                    registry = Registry.names, cargos = Util.Cargos(),
                    map_x = GSMap.GetMapSizeX(), map_y = GSMap.GetMapSizeY(),
                    date = GSDate.GetCurrentDate() });
    }

    function DrainEvents() {
        local handled = 0;
        while (GSEventController.IsEventWaiting() && handled < 64) {
            handled++;
            local ev = GSEventController.GetNextEvent();
            local type = ev.GetEventType();
            if (type == GSEvent.ET_ADMIN_PORT) {
                local request = GSEventAdminPort.Convert(ev).GetObject();
                if (request == null || typeof request != "table") {
                    GSLog.Error("ignoring malformed admin request");
                    continue;
                }
                if ("configure" in request) {
                    this.Configure(request);
                } else if ("items" in request) {
                    Executor.Run(request);
                } else if ("ping" in request) {
                    Util.Send({ id = ("id" in request) ? ("" + request.id) : "ping", pong = true, tick = GSController.GetTick() });
                }
                continue;
            }
            this.ForwardEvent(ev, type);
        }
    }

    function ForwardEvent(ev, type) {
        local msg = { push = "event", source = "game" };
        switch (type) {
            case GSEvent.ET_COMPANY_NEW:
                msg.type <- "company_new";
                msg.company <- GSEventCompanyNew.Convert(ev).GetCompanyID();
                break;
            case GSEvent.ET_COMPANY_BANKRUPT:
                msg.type <- "company_bankrupt";
                msg.company <- GSEventCompanyBankrupt.Convert(ev).GetCompanyID();
                Meter.CompanyRemoved(msg.company);
                break;
            case GSEvent.ET_COMPANY_IN_TROUBLE:
                msg.type <- "company_in_trouble";
                msg.company <- GSEventCompanyInTrouble.Convert(ev).GetCompanyID();
                break;
            case GSEvent.ET_SUBSIDY_OFFER:
                msg.type <- "subsidy_offer";
                msg.subsidy <- GSEventSubsidyOffer.Convert(ev).GetSubsidyID();
                break;
            case GSEvent.ET_SUBSIDY_AWARDED: {
                local e = GSEventSubsidyAwarded.Convert(ev);
                msg.type <- "subsidy_awarded";
                msg.subsidy <- e.GetSubsidyID();
                msg.company <- GSSubsidy.GetAwardedTo(e.GetSubsidyID());
                break;
            }
            case GSEvent.ET_SUBSIDY_EXPIRED:
                msg.type <- "subsidy_expired";
                msg.subsidy <- GSEventSubsidyExpired.Convert(ev).GetSubsidyID();
                break;
            case GSEvent.ET_INDUSTRY_OPEN:
                msg.type <- "industry_open";
                msg.industry <- GSEventIndustryOpen.Convert(ev).GetIndustryID();
                break;
            case GSEvent.ET_INDUSTRY_CLOSE:
                msg.type <- "industry_close";
                msg.industry <- GSEventIndustryClose.Convert(ev).GetIndustryID();
                break;
            case GSEvent.ET_VEHICLE_CRASHED: {
                local e = GSEventVehicleCrashed.Convert(ev);
                msg.type <- "vehicle_crashed";
                msg.vehicle <- e.GetVehicleID();
                local xy = Util.XY(e.GetCrashSite());
                msg.x <- xy.x; msg.y <- xy.y;
                if (GSVehicle.IsValidVehicle(e.GetVehicleID())) msg.company <- GSVehicle.GetOwner(e.GetVehicleID());
                break;
            }
            case GSEvent.ET_EXCLUSIVE_TRANSPORT_RIGHTS: {
                local e = GSEventExclusiveTransportRights.Convert(ev);
                msg.type <- "exclusive_transport_rights";
                msg.company <- e.GetCompanyID();
                msg.town <- e.GetTownID();
                break;
            }
            case GSEvent.ET_TOWN_FOUNDED:
                msg.type <- "town_founded";
                msg.town <- GSEventTownFounded.Convert(ev).GetTownID();
                break;
            case GSEvent.ET_ENGINE_AVAILABLE:
                msg.type <- "engine_available";
                msg.engine <- GSEventEngineAvailable.Convert(ev).GetEngineID();
                break;
            default:
                return;
        }
        msg.date <- GSDate.GetCurrentDate();
        Util.Send(msg);
    }
}
