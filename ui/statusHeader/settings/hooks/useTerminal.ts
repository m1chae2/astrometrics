import { useTerminalContext } from '../../context/TerminalContext';

export const useTerminal = () => {
    return useTerminalContext();
};
