class LLMEvalInfo extends GSInfo {
    function GetAuthor()      { return "openttd-bench"; }
    function GetName()        { return "LLMEval"; }
    function GetDescription() { return "Goal-free primitives for the OpenTTD bench control plane: namespaced lookup/build commands over the admin port."; }
    function GetVersion()     { return 3; }
    function GetDate()        { return "2026-09-06"; }
    function CreateInstance() { return "LLMEval"; }
    function GetShortName()   { return "LLM3"; }
    function GetAPIVersion()  { return "15"; }
    function MinVersionToLoad() { return 3; }

    function GetSettings() {
        // Minimal integer settings; every task-shaped value arrives through the
        // `configure` admin message, never through settings.
        AddSetting({
            name = "log_level", description = "Script log verbosity (0 quiet, 1 info, 2 debug)",
            min_value = 0, max_value = 2, easy_value = 1, medium_value = 1, hard_value = 1,
            custom_value = 1, flags = CONFIG_NONE
        });
    }
}

RegisterGS(LLMEvalInfo());
