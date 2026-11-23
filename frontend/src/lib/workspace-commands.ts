import type { MaritimeSkill } from "./maritime-api";
import type { UserPromptCommand } from "./account-settings-api";

export function skillDisplayName(skill: MaritimeSkill): string {
  return skill.display_name?.trim() || skill.name;
}

export function splitSkillsByReadiness(skills: MaritimeSkill[], accountId: string) {
  const ready: MaritimeSkill[] = [];
  const comingSoon: MaritimeSkill[] = [];
  const mine: MaritimeSkill[] = [];

  for (const skill of skills) {
    if (skill.owner_id === accountId) {
      mine.push(skill);
    } else if (skill.visibility === "builtin") {
      if (skill.status === "coming_soon") {
        comingSoon.push(skill);
      } else {
        ready.push(skill);
      }
    }
  }
  return { ready, comingSoon, mine };
}

export function matchPromptCommands(
  draft: string,
  commands: UserPromptCommand[],
): UserPromptCommand[] {
  if (!draft.startsWith("/")) return [];
  const firstLine = draft.split(/\r?\n/, 1)[0];
  if (firstLine.includes(" ")) return [];
  const needle = firstLine.slice(1).toLowerCase();
  return commands
    .filter(
      (cmd) =>
        !needle ||
        cmd.trigger.toLowerCase().includes(needle) ||
        cmd.title.toLowerCase().includes(needle),
    )
    .slice(0, 6);
}
