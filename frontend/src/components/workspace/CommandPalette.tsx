import { useEffect } from "react";
import {
  BookOpen,
  Compass,
  FolderOpen,
  KeyRound,
  MessageSquarePlus,
  Pause,
  Play,
  Search,
  SlidersHorizontal,
  Sparkles,
} from "lucide-react";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
  CommandShortcut,
} from "@/components/ui/command";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import type { Conversation, Project } from "@/lib/workbench-api";
import type { UserPromptCommand } from "@/lib/account-settings-api";
import type { UserCenterTab } from "./UserCenterDialog";

export interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  conversations: Conversation[];
  projects: Project[];
  commands: UserPromptCommand[];
  canPause?: boolean;
  canContinue?: boolean;
  onNewChat: () => void;
  onSelectConversation: (conversationId: string) => void;
  onStartInProject: (projectId: string) => void;
  onApplyCommand: (command: UserPromptCommand) => void;
  onNavigateOffice: (path: "/workspace/library" | "/workspace/skills" | "/workspace/research") => void;
  onOpenUserCenter: (tab: UserCenterTab) => void;
  onPauseRun?: () => void;
  onContinueRun?: () => void;
}

const ITEM_CLASS =
  "ws-cmdk-item flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm text-[#dbe3ec] cursor-pointer transition-colors data-[selected=true]:bg-[#1c2633] data-[selected=true]:text-[#ffffff]";

export function CommandPalette({
  open,
  onOpenChange,
  conversations,
  projects,
  commands,
  canPause,
  canContinue,
  onNewChat,
  onSelectConversation,
  onStartInProject,
  onApplyCommand,
  onNavigateOffice,
  onOpenUserCenter,
  onPauseRun,
  onContinueRun,
}: CommandPaletteProps) {
  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        onOpenChange(!open);
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, onOpenChange]);

  function runAndClose(fn: () => void) {
    onOpenChange(false);
    fn();
  }

  const recentChats = conversations.slice(0, 12);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="ws-dialog ws-cmdk-dialog border border-[#243243] bg-[#0c1117] text-[#eef2f6] p-0 gap-0 overflow-hidden max-w-xl w-[min(92vw,580px)] shadow-2xl"
        data-testid="command-palette"
      >
        <DialogTitle className="sr-only">全局命令面板</DialogTitle>
        <DialogDescription className="sr-only">搜索历史对话、调用快捷指令或切换工作台设置</DialogDescription>
        <Command className="ws-cmdk bg-transparent text-[#eef2f6] [&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-[11px] [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-[#7d8da0]">
          <div className="border-b border-white/10">
            <CommandInput
              placeholder="搜索对话、/ 指令或跳转页面…"
              className="text-sm text-[#eef2f6] placeholder:text-[#748496] h-12"
            />
          </div>
          <CommandList className="max-h-[380px] p-2">
            <CommandEmpty className="py-8 text-center text-sm text-[#7d8da0]">
              没有匹配的结果。
            </CommandEmpty>

            <CommandGroup heading="常用操作">
              <CommandItem className={ITEM_CLASS} onSelect={() => runAndClose(onNewChat)} value="新建对话 new chat">
                <MessageSquarePlus className="w-4 h-4 text-[#89aacc] shrink-0" />
                <span>新建对话</span>
                <CommandShortcut className="text-[#6e7e91]">主工作区</CommandShortcut>
              </CommandItem>
              {canPause && onPauseRun && (
                <CommandItem className={ITEM_CLASS} onSelect={() => runAndClose(onPauseRun)} value="暂停当前任务 pause">
                  <Pause className="w-4 h-4 text-[#89aacc] shrink-0" />
                  <span>暂停当前生成</span>
                </CommandItem>
              )}
              {canContinue && onContinueRun && (
                <CommandItem className={ITEM_CLASS} onSelect={() => runAndClose(onContinueRun)} value="继续处理任务 continue">
                  <Play className="w-4 h-4 text-[#89aacc] shrink-0" />
                  <span>继续处理当前任务</span>
                </CommandItem>
              )}
            </CommandGroup>

            {commands.length > 0 && (
              <>
                <CommandSeparator className="my-1.5 bg-white/10" />
                <CommandGroup heading="插入快捷 / 指令">
                  {commands.map((cmd) => (
                    <CommandItem
                      key={cmd.id}
                      className={ITEM_CLASS}
                      value={`/${cmd.trigger} ${cmd.title} ${cmd.content}`}
                      onSelect={() => runAndClose(() => onApplyCommand(cmd))}
                    >
                      <Sparkles className="w-4 h-4 text-[#89aacc] shrink-0" />
                      <span className="truncate">{cmd.title}</span>
                      <CommandShortcut className="text-[#89aacc] tracking-normal font-mono text-xs">
                        /{cmd.trigger}
                      </CommandShortcut>
                    </CommandItem>
                  ))}
                </CommandGroup>
              </>
            )}

            {recentChats.length > 0 && (
              <>
                <CommandSeparator className="my-1.5 bg-white/10" />
                <CommandGroup heading="最近对话">
                  {recentChats.map((chat) => (
                    <CommandItem
                      key={chat.id}
                      className={ITEM_CLASS}
                      value={`${chat.title || "未命名对话"} ${chat.id}`}
                      onSelect={() => runAndClose(() => onSelectConversation(chat.id))}
                    >
                      <Search className="w-3.5 h-3.5 text-[#7d8da0] shrink-0" />
                      <span className="truncate">{chat.title || "未命名对话"}</span>
                      <CommandShortcut className="text-[#748496] tracking-normal">
                        {chat.mode === "work" ? "工作" : "聊天"}
                      </CommandShortcut>
                    </CommandItem>
                  ))}
                </CommandGroup>
              </>
            )}

            {projects.length > 0 && (
              <>
                <CommandSeparator className="my-1.5 bg-white/10" />
                <CommandGroup heading="在项目中新建对话">
                  {projects.map((project) => (
                    <CommandItem
                      key={project.id}
                      className={ITEM_CLASS}
                      value={`项目 ${project.name}`}
                      onSelect={() => runAndClose(() => onStartInProject(project.id))}
                    >
                      <FolderOpen className="w-4 h-4 text-[#89aacc] shrink-0" />
                      <span className="truncate">在「{project.name}」新建对话</span>
                      <CommandShortcut className="text-[#748496] tracking-normal">
                        {project.conversation_count} 项
                      </CommandShortcut>
                    </CommandItem>
                  ))}
                </CommandGroup>
              </>
            )}

            <CommandSeparator className="my-1.5 bg-white/10" />
            <CommandGroup heading="导航与个人设置">
              <CommandItem className={ITEM_CLASS} value="资料库 library" onSelect={() => runAndClose(() => onNavigateOffice("/workspace/library"))}>
                <BookOpen className="w-4 h-4 text-[#8fa2b7] shrink-0" />
                <span>资料库</span>
              </CommandItem>
              <CommandItem className={ITEM_CLASS} value="技能目录 skills" onSelect={() => runAndClose(() => onNavigateOffice("/workspace/skills"))}>
                <Compass className="w-4 h-4 text-[#8fa2b7] shrink-0" />
                <span>技能目录</span>
              </CommandItem>
              <CommandItem className={ITEM_CLASS} value="海事检索 research" onSelect={() => runAndClose(() => onNavigateOffice("/workspace/research"))}>
                <Search className="w-4 h-4 text-[#8fa2b7] shrink-0" />
                <span>海事检索</span>
              </CommandItem>
              <CommandItem className={ITEM_CLASS} value="记忆与指令 memory slash" onSelect={() => runAndClose(() => onOpenUserCenter("memory"))}>
                <Sparkles className="w-4 h-4 text-[#8fa2b7] shrink-0" />
                <span>管理长期记忆与 / 指令</span>
              </CommandItem>
              <CommandItem className={ITEM_CLASS} value="工作习惯与界面设置 habits" onSelect={() => runAndClose(() => onOpenUserCenter("habits"))}>
                <SlidersHorizontal className="w-4 h-4 text-[#8fa2b7] shrink-0" />
                <span>工作习惯与界面偏好</span>
              </CommandItem>
              <CommandItem className={ITEM_CLASS} value="账号与密码 account password" onSelect={() => runAndClose(() => onOpenUserCenter("account"))}>
                <KeyRound className="w-4 h-4 text-[#8fa2b7] shrink-0" />
                <span>账号资料与修改密码</span>
              </CommandItem>
            </CommandGroup>
          </CommandList>
        </Command>
      </DialogContent>
    </Dialog>
  );
}
