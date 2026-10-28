class LLMCompanyInfo extends AIInfo {
    function GetAuthor()      { return "openttd-bench"; }
    function GetName()        { return "LLMCompany"; }
    function GetDescription() { return "Passive company holder for an LLM seat: applies relayed order edits and forwards AI-only events. Makes no decisions."; }
    function GetVersion()     { return 5; }
    function GetDate()        { return "2026-09-06"; }
    function CreateInstance() { return "LLMCompany"; }
    function GetShortName()   { return "LLMC"; }
    function GetAPIVersion()  { return "15"; }
    function UseAsRandomAI()  { return false; }
}

RegisterAI(LLMCompanyInfo());
