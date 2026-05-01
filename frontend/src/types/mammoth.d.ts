declare module "mammoth" {
  export interface ConvertOptions {
    arrayBuffer: ArrayBuffer;
  }
  export interface ConvertResult {
    value: string;
    messages: { type: string; message: string }[];
  }
  export function convertToHtml(options: ConvertOptions): Promise<ConvertResult>;
  export function extractRawText(options: ConvertOptions): Promise<ConvertResult>;
}
