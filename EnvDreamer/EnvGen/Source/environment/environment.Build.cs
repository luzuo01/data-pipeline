// We are cooked
using UnrealBuildTool;

public class environment : ModuleRules
{
    public environment(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

        PublicDependencyModuleNames.AddRange(new string[] {
            "Core",
            "CoreUObject",
            "Engine",
            "InputCore",
            "HTTP",
            "Json"
        });

        PrivateDependencyModuleNames.AddRange(new string[] {
            "Slate",
            "SlateCore",
            "DirectoryWatcher",
            "UMG"
            // NOTE: UMGEditor is editor-only; moved below
        });

        if (Target.bBuildEditor)
        {
            PrivateDependencyModuleNames.AddRange(new string[] {
                // Remote Control stack (ensure these three)
                "RemoteControl",
                "RemoteControlCommon",
                "RemoteControlLogic",
                // Helpful editor deps you’re already using
                "RemoteControlUI",
                "LevelEditor",
                "UnrealEd",
                "AssetTools",
                "AssetRegistry",
                "ContentBrowser",
                "EditorStyle",
                "EditorWidgets",
                "ToolMenus",
                "Blutility",
                "UMGEditor"
            });
        }
    }
}
