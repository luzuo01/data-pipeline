// We are cooked

using UnrealBuildTool;
using System.Collections.Generic;

public class environmentEditorTarget : TargetRules
{
	public environmentEditorTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Editor;
		DefaultBuildSettings = BuildSettingsVersion.V5;

		ExtraModuleNames.AddRange( new string[] { "environment" } );
	}
}
