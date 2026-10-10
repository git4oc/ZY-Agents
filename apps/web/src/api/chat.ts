// ZY-Agents 阶段 1 无文件上传端点：stub 为本地回显（保持签名），
// 附件消息以文件名占位继续走聊天链路，不发起网络请求。

export type UploadedImage = {
  object_path?: string
  url?: string
  signed_url?: string
  filename?: string
  content_type?: string
  size?: number
}

export type UploadedDocument = {
  object_path?: string
  url?: string
  signed_url?: string
  filename?: string
  content_type?: string
  size?: number
}

export async function uploadChatImage(_userId: string, file: File, _authToken?: string | null): Promise<UploadedImage> {
  return {
    filename: file.name,
    content_type: file.type,
    size: file.size,
  }
}

export async function uploadChatDocument(_userId: string, file: File, _authToken?: string | null): Promise<UploadedDocument> {
  return {
    filename: file.name,
    content_type: file.type,
    size: file.size,
  }
}
