import {
  Children,
  Fragment,
  isValidElement,
  useRef,
  type ReactElement,
  type ReactNode,
} from "react";
import * as Primitive from "@radix-ui/react-select";
import { Check, ChevronDown, ChevronUp } from "lucide-react";
import { encodeSelectValue, decodeSelectValue } from "./selectValue";

type OptionProps = {
  value?: string | number;
  disabled?: boolean;
  children?: ReactNode;
};
type SelectProps = {
  children: ReactNode;
  value: string | number;
  onValueChange: (value: string) => void;
  disabled?: boolean;
  required?: boolean;
  name?: string;
  id?: string;
  title?: string;
  className?: string;
  "aria-label"?: string;
  "aria-labelledby"?: string;
  "aria-describedby"?: string;
};

function optionNodes(children: ReactNode): ReactElement<OptionProps>[] {
  return Children.toArray(children).flatMap((child) => {
    if (!isValidElement(child)) return [];
    if (child.type === Fragment)
      return optionNodes((child.props as { children: ReactNode }).children);
    return child.type === "option" ? [child as ReactElement<OptionProps>] : [];
  });
}

function optionValue(option: ReactElement<OptionProps>): string {
  return String(option.props.value ?? option.props.children ?? "");
}

/** Accessible single-select with an anchored, viewport-aware option list. */
export function Select({
  children,
  value,
  onValueChange,
  disabled,
  required,
  name,
  id,
  title,
  className,
  "aria-label": ariaLabel,
  "aria-labelledby": ariaLabelledBy,
  "aria-describedby": ariaDescribedBy,
}: SelectProps) {
  const options = optionNodes(children);
  const current = String(value ?? "");
  const selected = options.find((option) => optionValue(option) === current);
  const trigger = useRef<HTMLButtonElement>(null);
  // A required empty selection stays truly empty in Radix's native form control.
  const encoded = required && current === "" ? "" : encodeSelectValue(current);
  return (
    <Primitive.Root
      value={encoded}
      disabled={disabled}
      required={required}
      name={name}
      onValueChange={(next) => {
        // A disabled parent fieldset disables its trigger even if the prop is unset.
        if (!trigger.current?.matches(":disabled"))
          onValueChange(decodeSelectValue(next));
      }}
    >
      <Primitive.Trigger
        ref={trigger}
        id={id}
        title={title}
        className={`select-control${className ? ` ${className}` : ""}`}
        aria-label={ariaLabel}
        aria-labelledby={ariaLabelledBy}
        aria-describedby={ariaDescribedBy}
      >
        <Primitive.Value placeholder={selected?.props.children ?? "请选择"} />
        <Primitive.Icon className="select-control-icon">
          <ChevronDown size={16} aria-hidden="true" />
        </Primitive.Icon>
      </Primitive.Trigger>
      <Primitive.Portal>
        <Primitive.Content
          className="select-menu"
          position="popper"
          side="bottom"
          align="start"
          sideOffset={6}
          collisionPadding={10}
        >
          <Primitive.ScrollUpButton className="select-scroll">
            <ChevronUp size={16} aria-hidden="true" />
          </Primitive.ScrollUpButton>
          <Primitive.Viewport className="select-viewport">
            {options.map((option) => {
              const optionId = encodeSelectValue(optionValue(option));
              return (
                <Primitive.Item
                  className="select-option"
                  value={optionId}
                  disabled={option.props.disabled}
                  key={option.key ?? optionId}
                >
                  <Primitive.ItemText>
                    {option.props.children}
                  </Primitive.ItemText>
                  <Primitive.ItemIndicator className="select-option-check">
                    <Check size={15} aria-hidden="true" />
                  </Primitive.ItemIndicator>
                </Primitive.Item>
              );
            })}
          </Primitive.Viewport>
          <Primitive.ScrollDownButton className="select-scroll">
            <ChevronDown size={16} aria-hidden="true" />
          </Primitive.ScrollDownButton>
        </Primitive.Content>
      </Primitive.Portal>
    </Primitive.Root>
  );
}
