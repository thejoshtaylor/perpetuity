"use client"

import {
  CircleCheckIcon,
  InfoIcon,
  Loader2Icon,
  OctagonXIcon,
  TriangleAlertIcon,
} from "lucide-react"
import { useTheme } from "next-themes"
import { Toaster as Sonner, type ToasterProps } from "sonner"

const Toaster = ({ ...props }: ToasterProps) => {
  const { theme = "system" } = useTheme()

  return (
    <Sonner
      theme={theme as ToasterProps["theme"]}
      className="toaster group"
      icons={{
        success: <CircleCheckIcon className="size-4" />,
        info: <InfoIcon className="size-4" />,
        warning: <TriangleAlertIcon className="size-4" />,
        error: <OctagonXIcon className="size-4" />,
        loading: <Loader2Icon className="size-4 animate-spin" />,
      }}
      style={
        {
          "--normal-bg": "var(--popover)",
          "--normal-text": "var(--popover-foreground)",
          "--normal-border": "var(--border)",
          "--border-radius": "var(--radius)",
        } as React.CSSProperties
      }
      // M005-oaptsz/S01: bump the toast close button to 44x44 so the
      // iOS-install toast (and any other dismissible toast) satisfies
      // the mobile-audit gate's >=44x44 CSS-px touch-target rule.
      toastOptions={{
        classNames: {
          closeButton:
            "!h-11 !w-11 !min-h-11 !min-w-11 !left-auto !right-2 !top-2 !rounded-full",
        },
      }}
      {...props}
    />
  )
}

export { Toaster }
