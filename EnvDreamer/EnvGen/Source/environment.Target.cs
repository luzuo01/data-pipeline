// We are cooked

using UnrealBuildTool;
using System.Collections.Generic;

public class environmentTarget : TargetRules
{
	public environmentTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Game;
		DefaultBuildSettings = BuildSettingsVersion.V5;

		ExtraModuleNames.AddRange( new string[] { "environment" } );
	}
}
