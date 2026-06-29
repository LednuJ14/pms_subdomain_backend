-- =====================================================
-- Chat System Database Schema
-- =====================================================
-- This file contains SQL commands to create the chat system tables
-- Run these commands manually in your MySQL database
-- =====================================================

-- =====================================================
-- Table: chats
-- Purpose: Stores chat conversations between tenants and property managers
-- =====================================================
CREATE TABLE IF NOT EXISTS `chats` (
  `id` INT(11) NOT NULL AUTO_INCREMENT,
  `tenant_id` INT(11) NOT NULL,
  `property_id` INT(11) NOT NULL,
  `subject` VARCHAR(255) NULL DEFAULT 'New Conversation',
  `status` ENUM('active', 'archived', 'closed') NOT NULL DEFAULT 'active',
  `last_message_at` DATETIME NULL DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  INDEX `idx_chats_tenant_id` (`tenant_id`),
  INDEX `idx_chats_property_id` (`property_id`),
  INDEX `idx_chats_status` (`status`),
  INDEX `idx_chats_last_message_at` (`last_message_at`),
  CONSTRAINT `fk_chats_tenant` 
    FOREIGN KEY (`tenant_id`) 
    REFERENCES `tenants` (`id`) 
    ON DELETE CASCADE 
    ON UPDATE CASCADE,
  CONSTRAINT `fk_chats_property` 
    FOREIGN KEY (`property_id`) 
    REFERENCES `properties` (`id`) 
    ON DELETE CASCADE 
    ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =====================================================
-- Table: messages
-- Purpose: Stores individual messages within chat conversations
-- =====================================================
CREATE TABLE IF NOT EXISTS `messages` (
  `id` INT(11) NOT NULL AUTO_INCREMENT,
  `chat_id` INT(11) NOT NULL,
  `sender_id` INT(11) NOT NULL COMMENT 'User ID of the sender',
  `sender_type` ENUM('tenant', 'property_manager') NOT NULL COMMENT 'Type of sender',
  `content` TEXT NOT NULL,
  `is_read` TINYINT(1) NOT NULL DEFAULT 0,
  `read_at` DATETIME NULL DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  INDEX `idx_messages_chat_id` (`chat_id`),
  INDEX `idx_messages_sender_id` (`sender_id`),
  INDEX `idx_messages_sender_type` (`sender_type`),
  INDEX `idx_messages_is_read` (`is_read`),
  INDEX `idx_messages_created_at` (`created_at`),
  CONSTRAINT `fk_messages_chat` 
    FOREIGN KEY (`chat_id`) 
    REFERENCES `chats` (`id`) 
    ON DELETE CASCADE 
    ON UPDATE CASCADE,
  CONSTRAINT `fk_messages_sender` 
    FOREIGN KEY (`sender_id`) 
    REFERENCES `users` (`id`) 
    ON DELETE CASCADE 
    ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =====================================================
-- Notes:
-- =====================================================
-- 1. The chats table links tenants to properties (property managers are identified via property.manager_id)
-- 2. Each chat belongs to one tenant and one property
-- 3. Messages store the sender's user_id and sender_type to identify who sent the message
-- 4. Property managers are identified by checking if the sender is the manager of the property
-- 5. The last_message_at field in chats is updated via triggers or application logic
-- 6. All foreign keys use CASCADE to maintain referential integrity
-- =====================================================

-- =====================================================
-- Optional: Trigger to update last_message_at in chats table
-- =====================================================
DELIMITER $$

CREATE TRIGGER `update_chat_last_message` 
AFTER INSERT ON `messages`
FOR EACH ROW
BEGIN
  UPDATE `chats` 
  SET `last_message_at` = NEW.created_at 
  WHERE `id` = NEW.chat_id;
END$$

DELIMITER ;

-- =====================================================
-- Verification Queries (run after creating tables)
-- =====================================================
-- Check if tables were created:
-- SHOW TABLES LIKE 'chats';
-- SHOW TABLES LIKE 'messages';

-- Check table structure:
-- DESCRIBE chats;
-- DESCRIBE messages;

-- Check indexes:
-- SHOW INDEX FROM chats;
-- SHOW INDEX FROM messages;

