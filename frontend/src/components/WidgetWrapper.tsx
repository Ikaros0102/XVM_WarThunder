import React, { useRef, useState } from 'react'
import { WidgetLayout } from '../types'

interface WidgetWrapperProps {
  id: string
  title: string
  layout: WidgetLayout
  isOverlay: boolean
  editLayout: boolean
  onChangeLayout: (id: string, patch: Partial<WidgetLayout>) => void
  children: React.ReactNode
}

export const WidgetWrapper: React.FC<WidgetWrapperProps> = ({
  id,
  title,
  layout,
  isOverlay,
  editLayout,
  onChangeLayout,
  children,
}) => {
  const dragState = useRef<{
    pointerId: number
    startX: number
    startY: number
    origX: number
    origY: number
  } | null>(null)

  const [localPos, setLocalPos] = useState<{ x: number; y: number } | null>(null)
  const isRightAnchored = (layout.anchor_x ?? (id === 'enemy_panel' ? 'right' : 'left')) === 'right'

  if (!layout.enabled && !editLayout) {
    return null
  }

  const currentX = localPos ? localPos.x : layout.x
  const currentY = localPos ? localPos.y : layout.y

  const clampPos = (dx: number, dy: number, origX: number, origY: number) => {
    const maxX = typeof window !== 'undefined' ? Math.max(0, window.innerWidth - 80) : 4000
    const maxY = typeof window !== 'undefined' ? Math.max(0, window.innerHeight - 40) : 2000
    const rawX = isRightAnchored ? origX - dx : origX + dx
    return {
      x: Math.max(0, Math.min(maxX, Math.round(rawX))),
      y: Math.max(0, Math.min(maxY, Math.round(origY + dy))),
    }
  }

  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!editLayout) return
    e.preventDefault()
    e.currentTarget.setPointerCapture(e.pointerId)
    dragState.current = {
      pointerId: e.pointerId,
      startX: e.clientX,
      startY: e.clientY,
      origX: layout.x,
      origY: layout.y,
    }
    setLocalPos({ x: layout.x, y: layout.y })
  }

  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragState.current || dragState.current.pointerId !== e.pointerId) return
    const dx = e.clientX - dragState.current.startX
    const dy = e.clientY - dragState.current.startY
    setLocalPos(clampPos(dx, dy, dragState.current.origX, dragState.current.origY))
  }

  const handlePointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragState.current || dragState.current.pointerId !== e.pointerId) return
    const dx = e.clientX - dragState.current.startX
    const dy = e.clientY - dragState.current.startY
    const nextPos = clampPos(dx, dy, dragState.current.origX, dragState.current.origY)
    dragState.current = null
    setLocalPos(null)
    onChangeLayout(id, {
      x: nextPos.x,
      y: nextPos.y,
      anchor_x: isRightAnchored ? 'right' : 'left',
    })
  }

  const style: React.CSSProperties = isOverlay
    ? {
        position: 'fixed',
        ...(isRightAnchored ? { right: `${currentX}px` } : { left: `${currentX}px` }),
        top: `${currentY}px`,
        transform: `scale(${layout.scale})`,
        transformOrigin: isRightAnchored ? 'top right' : 'top left',
      }
    : {}

  return (
    <div
      className={`widget-shell ${isOverlay ? 'widget-overlay' : 'widget-board'} ${
        isRightAnchored ? 'widget-anchor-right' : ''
      } ${editLayout ? 'widget-editing' : ''} ${!layout.enabled ? 'widget-disabled-preview' : ''}`}
      style={style}
    >
      {editLayout && (
        <div
          className="widget-edit-bar"
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerUp}
        >
          <span className="widget-edit-title">{title}</span>
          <div className="widget-edit-controls" onPointerDown={(e) => e.stopPropagation()}>
            <label className="widget-scale-label">
              <span>{layout.scale.toFixed(2)}x</span>
              <input
                type="range"
                min={0.5}
                max={1.8}
                step={0.05}
                value={layout.scale}
                onChange={(e) => onChangeLayout(id, { scale: Number(e.target.value) })}
                aria-label={`Масштаб виджета ${title}`}
              />
            </label>
            <label className="widget-scale-label">
              <span>{Math.round((layout.opacity ?? 1) * 100)}%</span>
              <input
                type="range"
                min={0.1}
                max={1.0}
                step={0.05}
                value={layout.opacity ?? 1}
                onChange={(e) => onChangeLayout(id, { opacity: Number(e.target.value) })}
                aria-label={`Прозрачность виджета ${title}`}
              />
            </label>
            <button
              type="button"
              className="btn-chip"
              onClick={() => onChangeLayout(id, { enabled: !layout.enabled })}
            >
              {layout.enabled ? 'Скрыть' : 'Показать'}
            </button>
          </div>
        </div>
      )}
      <div className="widget-content" style={{ opacity: layout.opacity ?? 1 }}>
        {children}
      </div>
    </div>
  )
}
